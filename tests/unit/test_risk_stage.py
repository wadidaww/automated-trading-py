from __future__ import annotations

from typing import Any

import pytest

from trader.api.client import OrderStatusResponse, PortfolioResponse, PositionResponse
from trader.core.clock import SimulatedClock
from trader.model.base import Signal
from trader.pipeline.risk_stage import PreTradeLimits, RiskStage
from trader.pipeline.signal_stage import TradeSignal
from trader.risk.kill_switch import KillSwitch
from trader.risk.risk_engine import RiskEngine
from trader.risk.throttle import OrderRateThrottle
from helpers import make_position, make_stock_info

SYMBOL = "HK.00700"


class FakeClient:
    """Broker stub with mutable state; set ``fail`` to make every query raise."""

    def __init__(self) -> None:
        self.total_assets = 1_000_000.0
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.positions: list[PositionResponse] = []
        self.orders: list[OrderStatusResponse] = []
        self.lot_size: int | None = 100
        self.fail = False

    async def get_portfolio(self) -> PortfolioResponse:
        if self.fail:
            raise RuntimeError("opend down")
        return PortfolioResponse(
            account_id=1,
            total_assets=self.total_assets,
            market_value=0.0,
            cash=self.total_assets,
            available_cash=self.total_assets,
            unrealized_pnl=self.unrealized_pnl,
            realized_pnl=self.realized_pnl,
        )

    async def get_positions(self) -> list[PositionResponse]:
        return self.positions

    async def list_orders(self) -> list[OrderStatusResponse]:
        return self.orders

    async def get_stock_info(self, symbol: str) -> Any:
        info = make_stock_info(symbol, 300.0)
        info.lot_size = self.lot_size
        return info


def _stage(
    client: FakeClient,
    *,
    max_order_qty: int = 10_000,
    max_order_notional: int = 100_000_000,
    max_daily_loss_minor: int = 1_000_000,
    max_open_orders: int = 10,
    per_second: int = 10,
    per_30s: int = 14,
    kill_switch: KillSwitch | None = None,
) -> RiskStage:
    engine = RiskEngine(
        max_symbol_notional_minor=50_000_000,
        max_portfolio_notional_minor=100_000_000,
        max_daily_loss_minor=max_daily_loss_minor,
        max_open_orders=max_open_orders,
        concentration_limit_pct=0.5,
    )
    return RiskStage(
        engine,
        client=client,  # type: ignore[arg-type]
        limits=PreTradeLimits(
            max_order_qty=max_order_qty,
            max_order_notional_minor=max_order_notional,
            price_band_pct=0.02,
        ),
        kill_switch=kill_switch or KillSwitch(),
        throttle=OrderRateThrottle(per_second, per_30s, clock=lambda: 0.0),
        strategy="test",
        clock=SimulatedClock(1_700_000_000_000_000_000),
    )


def _signal(
    signal: Signal = Signal.BUY, price: float = 300.1, confidence: float = 0.9
) -> TradeSignal:
    return TradeSignal(symbol=SYMBOL, signal=signal, confidence=confidence, price=price)


async def test_buy_is_rounded_to_tick_and_lot() -> None:
    stage = _stage(FakeClient())
    intent = await stage.process(_signal(price=300.13))
    assert intent is not None
    assert intent.side == "BUY"
    assert intent.limit_price == pytest.approx(300.0)  # 0.2 tick at 200-500, BUY rounds down
    assert intent.qty > 0
    assert intent.qty % 100 == 0
    assert intent.client_order_id.startswith("c")
    assert intent.reference_price == pytest.approx(300.13)


async def test_hold_never_becomes_an_order() -> None:
    stage = _stage(FakeClient())
    assert await stage.process(_signal(Signal.HOLD)) is None
    assert stage.reject_reasons == {"not_actionable": 1}


async def test_fails_closed_when_broker_state_unavailable() -> None:
    client = FakeClient()
    client.fail = True
    stage = _stage(client)
    assert await stage.process(_signal()) is None
    assert stage.reject_reasons == {"state_unavailable": 1}


async def test_rejects_without_lot_size() -> None:
    client = FakeClient()
    client.lot_size = None
    stage = _stage(client)
    assert await stage.process(_signal()) is None
    assert stage.reject_reasons == {"no_lot_size": 1}


async def test_sell_without_position_is_rejected() -> None:
    stage = _stage(FakeClient())
    assert await stage.process(_signal(Signal.SELL)) is None
    assert stage.reject_reasons == {"size_zero": 1}


async def test_sell_is_capped_at_can_sell_qty() -> None:
    client = FakeClient()
    client.positions = [make_position(SYMBOL, 500, can_sell_qty=250, nominal_price=300.0)]
    stage = _stage(client)
    intent = await stage.process(_signal(Signal.SELL, price=300.1))
    assert intent is not None
    assert intent.side == "SELL"
    assert intent.qty == 200  # can_sell 250 rounded down to the 100-share lot
    assert intent.limit_price == pytest.approx(300.2)  # SELL rounds up


async def test_max_order_qty_caps_size() -> None:
    stage = _stage(FakeClient(), max_order_qty=150)
    intent = await stage.process(_signal())
    assert intent is not None
    assert intent.qty == 100


async def test_daily_loss_breach_trips_latched_kill_switch() -> None:
    client = FakeClient()
    client.realized_pnl = -8_000.0
    client.unrealized_pnl = -3_000.0
    kill_switch = KillSwitch()
    stage = _stage(client, max_daily_loss_minor=1_000_000, kill_switch=kill_switch)
    assert await stage.process(_signal()) is None
    assert kill_switch.reason == "daily_loss_limit"

    client.realized_pnl = client.unrealized_pnl = 0.0
    assert await stage.process(_signal()) is None
    assert stage.reject_reasons["kill_switch"] == 1


async def test_working_orders_count_towards_open_order_limit() -> None:
    client = FakeClient()
    client.orders = [
        OrderStatusResponse(
            order_id="1", status="SUBMITTED", symbol=SYMBOL, order_side="BUY", qty=100, price=300.0
        ),
        OrderStatusResponse(
            order_id="2", status="TIMEOUT", symbol=SYMBOL, order_side="BUY", qty=100, price=300.0
        ),
        OrderStatusResponse(
            order_id="3",
            status="FILLED_ALL",
            symbol=SYMBOL,
            order_side="BUY",
            qty=100,
            dealt_qty=100,
            price=300.0,
        ),
    ]
    stage = _stage(client, max_open_orders=2)
    assert await stage.process(_signal()) is None
    assert stage.reject_reasons == {"open_orders_limit": 1}


async def test_order_rate_throttle_rejects() -> None:
    stage = _stage(FakeClient(), per_second=1)
    assert await stage.process(_signal()) is not None
    assert await stage.process(_signal()) is None
    assert stage.reject_reasons == {"order_rate_limit": 1}


async def test_client_order_ids_are_unique_per_intent() -> None:
    stage = _stage(FakeClient())
    first = await stage.process(_signal())
    second = await stage.process(_signal())
    assert first is not None
    assert second is not None
    assert first.client_order_id != second.client_order_id
