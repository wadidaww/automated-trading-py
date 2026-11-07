"""PreTradeRisk used directly, without the pipeline (as scheduled jobs will)."""

from __future__ import annotations

import pytest

from trader.core.clock import SimulatedClock
from trader.core.orders import OrderIntent
from trader.ports.models import (
    OrderStatusResponse,
    PortfolioResponse,
    PositionResponse,
    StockInfoResponse,
)
from trader.risk.kill_switch import KillSwitch
from trader.risk.portfolio_state import load_portfolio_state
from trader.risk.pre_trade import PreTradeLimits, PreTradeRequest, PreTradeRisk, Rejection
from trader.risk.risk_engine import RiskEngine
from trader.risk.throttle import OrderRateThrottle
from helpers import make_position, make_stock_info

SYMBOL = "HK.00700"


class _Broker:
    def __init__(self) -> None:
        self.positions: list[PositionResponse] = []
        self.orders: list[OrderStatusResponse] = []

    async def get_portfolio(self) -> PortfolioResponse:
        return PortfolioResponse(
            account_id=1,
            total_assets=1_000_000.0,
            market_value=0.0,
            cash=1_000_000.0,
            available_cash=1_000_000.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
        )

    async def get_positions(self) -> list[PositionResponse]:
        return self.positions

    async def list_orders(self) -> list[OrderStatusResponse]:
        return self.orders

    async def get_stock_info(self, symbol: str) -> StockInfoResponse:
        return make_stock_info(symbol, 300.0)


def _risk(broker: _Broker, kill_switch: KillSwitch | None = None) -> PreTradeRisk:
    return PreTradeRisk(
        RiskEngine(
            max_symbol_notional_minor=50_000_000,
            max_portfolio_notional_minor=100_000_000,
            max_daily_loss_minor=1_000_000,
            max_open_orders=10,
            concentration_limit_pct=0.5,
        ),
        client=broker,  # type: ignore[arg-type]
        limits=PreTradeLimits(
            max_order_qty=10_000, max_order_notional_minor=100_000_000, price_band_pct=0.02
        ),
        kill_switch=kill_switch or KillSwitch(),
        throttle=OrderRateThrottle(10, 14, clock=lambda: 0.0),
        strategy="job",
        clock=SimulatedClock(1_700_000_000_000_000_000),
    )


async def test_check_returns_a_rounded_intent() -> None:
    result = await _risk(_Broker()).check(
        PreTradeRequest(symbol=SYMBOL, side="BUY", confidence=0.9, price=300.13)
    )
    assert isinstance(result, OrderIntent)
    assert result.limit_price == pytest.approx(300.0)
    assert result.qty > 0
    assert result.qty % 100 == 0
    assert result.strategy == "job"


async def test_check_rejects_when_kill_switch_is_tripped() -> None:
    kill_switch = KillSwitch()
    kill_switch.trip("test")
    result = await _risk(_Broker(), kill_switch).check(
        PreTradeRequest(symbol=SYMBOL, side="BUY", confidence=0.9, price=300.0)
    )
    assert result == Rejection("kill_switch")


async def test_sell_without_position_is_size_zero() -> None:
    result = await _risk(_Broker()).check(
        PreTradeRequest(symbol=SYMBOL, side="SELL", confidence=0.9, price=300.0)
    )
    assert result == Rejection("size_zero")


async def test_portfolio_state_counts_working_buys() -> None:
    broker = _Broker()
    broker.positions = [make_position(SYMBOL, 100, market_value=30_000.0)]
    broker.orders = [
        OrderStatusResponse(
            order_id="1",
            status="SUBMITTED",
            symbol=SYMBOL,
            order_side="BUY",
            qty=200,
            dealt_qty=100,
            price=300.0,
        )
    ]
    state = await load_portfolio_state(broker, SYMBOL)  # type: ignore[arg-type]
    assert state.open_orders == 1
    assert state.symbol_notional_minor == 3_000_000 + 3_000_000
    assert state.can_sell_qty == 100
