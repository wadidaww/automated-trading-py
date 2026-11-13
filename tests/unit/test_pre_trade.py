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
from trader.risk.pre_trade import (
    PreTradeLimits,
    PreTradeRequest,
    PreTradeRisk,
    Rejection,
    VolTargetSizing,
)
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


def _risk(
    broker: _Broker,
    kill_switch: KillSwitch | None = None,
    vol_target: VolTargetSizing | None = None,
    max_order_notional_minor: int = 100_000_000,
) -> PreTradeRisk:
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
            max_order_qty=10_000,
            max_order_notional_minor=max_order_notional_minor,
            price_band_pct=0.02,
        ),
        kill_switch=kill_switch or KillSwitch(),
        throttle=OrderRateThrottle(10, 14, clock=lambda: 0.0),
        strategy="job",
        clock=SimulatedClock(1_700_000_000_000_000_000),
        vol_target=vol_target,
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


# --- volatility-target sizing (requests that carry a target exposure) -------------------------
# Portfolio 1,000,000, weight 0.10: the symbol's budget is 100,000; the band is 10,000.
SIZING = VolTargetSizing(target_vol_annual=0.60, symbol_weight=0.10, rebalance_band=0.10)


def _target(exposure: float | None, vol: float | None = 0.30, side: str = "BUY") -> PreTradeRequest:
    return PreTradeRequest(
        symbol=SYMBOL,
        side=side,  # type: ignore[arg-type]
        confidence=1.0,
        price=300.0,
        target_exposure=exposure,
        realised_vol=vol,
    )


def _holding(shares: int) -> _Broker:
    broker = _Broker()
    broker.positions = [make_position(SYMBOL, shares, market_value=shares * 300.0)]
    return broker


async def test_target_buys_the_gap_to_the_symbol_budget() -> None:
    result = await _risk(_Broker(), vol_target=SIZING).check(_target(1.0))
    assert isinstance(result, OrderIntent)
    assert (result.side, result.qty) == ("BUY", 300)  # 100,000 / 300 rounded down to the lot


async def test_target_shrinks_when_volatility_is_above_the_target() -> None:
    result = await _risk(_Broker(), vol_target=SIZING).check(_target(1.0, vol=1.20))
    assert isinstance(result, OrderIntent)
    assert result.qty == 100  # scale 0.5: 50,000 / 300 rounded down


async def test_target_never_exceeds_the_budget_when_volatility_is_low() -> None:
    result = await _risk(_Broker(), vol_target=SIZING).check(_target(1.0, vol=0.05))
    assert isinstance(result, OrderIntent)
    assert result.qty == 300  # the scale is capped at 1: no leverage


def _working(side: str, qty: float, price: float = 300.0) -> OrderStatusResponse:
    return OrderStatusResponse(
        order_id="1",
        status="SUBMITTED",
        symbol=SYMBOL,
        order_side=side,
        qty=qty,
        dealt_qty=0,
        price=price,
    )


@pytest.mark.parametrize(
    ("held", "working", "request_"),
    [
        (0, _working("BUY", 100), _target(1.0)),  # resting entry: do not stack another
        (300, _working("SELL", 100), _target(0.5)),  # resting trim: do not repeat it
        (300, _working("SELL", 300), _target(0.0, vol=None, side="SELL")),  # resting exit
        (200, _working("SELL", 100), _target(1.0)),  # never buy against the resting sell
    ],
)
async def test_target_waits_while_an_order_for_the_symbol_is_working(
    held: int, working: OrderStatusResponse, request_: PreTradeRequest
) -> None:
    broker = _holding(held) if held else _Broker()
    broker.orders = [working]
    result = await _risk(broker, vol_target=SIZING).check(request_)
    assert result == Rejection("working_order_pending")


async def test_target_ignores_working_orders_for_other_symbols() -> None:
    broker = _Broker()
    broker.orders = [_working("BUY", 100).model_copy(update={"symbol": "HK.09988"})]
    result = await _risk(broker, vol_target=SIZING).check(_target(1.0))
    assert isinstance(result, OrderIntent)


@pytest.mark.parametrize("market_value", [0.0, -5.0])
async def test_target_rejects_a_held_position_without_market_value(market_value: float) -> None:
    broker = _Broker()
    broker.positions = [make_position(SYMBOL, 300, market_value=market_value)]
    result = await _risk(broker, vol_target=SIZING).check(_target(1.0))
    assert result == Rejection("no_position_value")


@pytest.mark.parametrize("field", ["quantity", "can_sell_qty"])
async def test_target_rejects_non_finite_position_quantities(field: str) -> None:
    broker = _Broker()
    position = make_position(SYMBOL, 300, market_value=90_000.0)
    broker.positions = [position.model_copy(update={field: float("nan")})]
    for request_ in (_target(0.5), _target(0.0, vol=None, side="SELL")):
        assert await _risk(broker, vol_target=SIZING).check(request_) == Rejection(
            "state_unavailable"
        )


async def test_target_rejects_a_portfolio_worth_less_than_the_position() -> None:
    broker = _Broker()
    broker.positions = [make_position(SYMBOL, 300, market_value=2_000_000.0)]
    result = await _risk(broker, vol_target=SIZING).check(_target(1.0))
    assert result == Rejection("implausible_portfolio_value")


async def test_buy_request_with_a_zero_target_rejects() -> None:
    result = await _risk(_holding(300), vol_target=SIZING).check(_target(0.0, side="BUY"))
    assert result == Rejection("invalid_target")


async def test_full_exit_is_capped_per_order() -> None:
    risk = _risk(_holding(300), vol_target=SIZING, max_order_notional_minor=3_000_000)
    result = await risk.check(_target(0.0, vol=None, side="SELL"))
    assert isinstance(result, OrderIntent)
    assert (result.side, result.qty) == ("SELL", 100)


async def test_target_within_the_band_does_not_trade() -> None:
    broker = _Broker()
    broker.positions = [make_position(SYMBOL, 300, market_value=95_000.0)]
    result = await _risk(broker, vol_target=SIZING).check(_target(1.0))
    assert result == Rejection("within_rebalance_band")


async def test_target_below_position_trims_with_a_sell() -> None:
    result = await _risk(_holding(300), vol_target=SIZING).check(_target(0.5))
    assert isinstance(result, OrderIntent)
    assert (result.side, result.qty) == ("SELL", 100)  # 90,000 held, 50,000 wanted


async def test_target_trim_never_sells_more_than_sellable() -> None:
    broker = _Broker()
    broker.positions = [make_position(SYMBOL, 300, can_sell_qty=0, market_value=90_000.0)]
    result = await _risk(broker, vol_target=SIZING).check(_target(0.5))
    assert result == Rejection("size_zero")


async def test_target_zero_sells_everything_without_needing_volatility() -> None:
    result = await _risk(_holding(300), vol_target=SIZING).check(
        _target(0.0, vol=None, side="SELL")
    )
    assert isinstance(result, OrderIntent)
    assert (result.side, result.qty) == ("SELL", 300)


async def test_target_zero_without_position_places_nothing() -> None:
    result = await _risk(_Broker(), vol_target=SIZING).check(_target(0.0, side="SELL"))
    assert result == Rejection("at_target")


@pytest.mark.parametrize("vol", [None, float("nan"), 0.0, -0.2, float("inf")])
async def test_target_entry_without_usable_volatility_rejects(vol: float | None) -> None:
    result = await _risk(_Broker(), vol_target=SIZING).check(_target(1.0, vol=vol))
    assert result == Rejection("no_volatility")


@pytest.mark.parametrize("exposure", [1.5, -0.1, float("nan")])
async def test_target_outside_zero_to_one_rejects(exposure: float) -> None:
    result = await _risk(_Broker(), vol_target=SIZING).check(_target(exposure))
    assert result == Rejection("invalid_target")


async def test_sell_request_with_a_positive_target_rejects() -> None:
    result = await _risk(_holding(300), vol_target=SIZING).check(_target(0.5, side="SELL"))
    assert result == Rejection("invalid_target")


async def test_target_request_rejects_when_sizing_is_off() -> None:
    result = await _risk(_Broker()).check(_target(1.0))
    assert result == Rejection("target_sizing_disabled")


async def test_target_order_still_obeys_the_per_order_notional_cap() -> None:
    risk = _risk(_Broker(), vol_target=SIZING, max_order_notional_minor=3_000_000)
    result = await risk.check(_target(1.0))
    assert isinstance(result, OrderIntent)
    assert result.qty == 100  # capped at 30,000 notional


async def test_target_order_still_obeys_the_kill_switch_and_price_band() -> None:
    kill_switch = KillSwitch()
    kill_switch.trip("test")
    assert await _risk(_Broker(), kill_switch, SIZING).check(_target(1.0)) == Rejection(
        "kill_switch"
    )
    far = PreTradeRequest(
        symbol=SYMBOL,
        side="BUY",
        confidence=1.0,
        price=330.0,
        target_exposure=1.0,
        realised_vol=0.3,
    )
    assert await _risk(_Broker(), vol_target=SIZING).check(far) == Rejection("price_band")


async def test_requests_without_a_target_keep_kelly_sizing_when_sizing_is_on() -> None:
    plain = PreTradeRequest(symbol=SYMBOL, side="BUY", confidence=0.9, price=300.0)
    with_sizing = await _risk(_Broker(), vol_target=SIZING).check(plain)
    without = await _risk(_Broker()).check(plain)
    assert isinstance(with_sizing, OrderIntent)
    assert isinstance(without, OrderIntent)
    assert with_sizing.qty == without.qty
