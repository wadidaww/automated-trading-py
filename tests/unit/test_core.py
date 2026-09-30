"""Tests for the dependency-free core domain layer."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from trader.core.clock import SimulatedClock
from trader.core.market_rules import (
    HKSessionCalendar,
    SessionPhase,
    is_on_tick,
    round_down_to_lot,
    round_to_tick,
    tick_size,
)
from trader.core.orders import (
    OrderIntent,
    OrderStatus,
    ManagedOrder,
    can_transition,
    from_futu_status,
    make_client_order_id,
)
from trader.core.symbols import market_of, normalize_symbol


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("700.HK", "HK.00700"),
        ("HK.700", "HK.00700"),
        ("hk.00700", "HK.00700"),
        ("0005.HK", "HK.00005"),
        ("aapl.us", "US.AAPL"),
        ("US.AAPL", "US.AAPL"),
        ("SH.600519", "SH.600519"),
    ],
)
def test_normalize_symbol(raw: str, expected: str) -> None:
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["700", "XX.700", "HK.ABC", ".HK", ""])
def test_normalize_symbol_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError, match="symbol|market|numeric"):
        normalize_symbol(raw)


def test_market_of() -> None:
    assert market_of("HK.00700") == "HK"


@pytest.mark.parametrize(
    ("price", "tick"),
    [
        (0.2, 0.001),
        (0.3, 0.005),
        (9.99, 0.01),
        (15.0, 0.02),
        (50.0, 0.05),
        (150.0, 0.1),
        (385.0, 0.2),
        (600.0, 0.5),
        (1500.0, 1.0),
        (3000.0, 2.0),
        (6000.0, 5.0),
    ],
)
def test_hk_tick_ladder(price: float, tick: float) -> None:
    assert tick_size("HK.00700", price) == tick


def test_round_to_tick_is_passive() -> None:
    assert round_to_tick("HK.00700", 385.37, "BUY") == 385.2
    assert round_to_tick("HK.00700", 385.37, "SELL") == 385.4
    assert round_to_tick("HK.00700", 385.4, "BUY") == 385.4
    assert round_to_tick("US.AAPL", 0.12345, "SELL") == 0.1235
    assert round_to_tick("US.AAPL", 187.123, "BUY") == 187.12


def test_is_on_tick() -> None:
    assert is_on_tick("HK.00700", 385.4)
    assert not is_on_tick("HK.00700", 385.3)


def test_round_down_to_lot() -> None:
    assert round_down_to_lot(250, 100) == 200
    assert round_down_to_lot(99, 100) == 0
    assert round_down_to_lot(500, 0) == 0


def _hkt_ns(year: int, month: int, day: int, hour: int, minute: int) -> int:
    # HKT is UTC+8.
    return int(datetime(year, month, day, hour - 8, minute, tzinfo=UTC).timestamp() * 1e9)


def test_hk_session_phases() -> None:
    cal = HKSessionCalendar(holidays=frozenset({date(2026, 10, 1)}))
    # 2026-09-30 is a Wednesday.
    assert cal.phase(_hkt_ns(2026, 9, 30, 9, 15)) is SessionPhase.PRE_OPEN
    assert cal.phase(_hkt_ns(2026, 9, 30, 10, 0)) is SessionPhase.CONTINUOUS
    assert cal.phase(_hkt_ns(2026, 9, 30, 12, 30)) is SessionPhase.LUNCH
    assert cal.phase(_hkt_ns(2026, 9, 30, 15, 59)) is SessionPhase.CONTINUOUS
    assert cal.phase(_hkt_ns(2026, 9, 30, 16, 5)) is SessionPhase.CLOSING_AUCTION
    assert cal.phase(_hkt_ns(2026, 9, 30, 17, 0)) is SessionPhase.CLOSED
    assert cal.phase(_hkt_ns(2026, 10, 1, 10, 0)) is SessionPhase.CLOSED  # holiday
    assert cal.phase(_hkt_ns(2026, 10, 3, 10, 0)) is SessionPhase.CLOSED  # Saturday


def test_half_day_closes_at_noon() -> None:
    cal = HKSessionCalendar(half_days=frozenset({date(2026, 12, 24)}))
    assert cal.is_continuous(_hkt_ns(2026, 12, 24, 11, 0))
    assert not cal.is_continuous(_hkt_ns(2026, 12, 24, 14, 0))


def test_futu_status_mapping_covers_documented_statuses() -> None:
    assert from_futu_status("FILLED_PART") is OrderStatus.PARTIALLY_FILLED
    assert from_futu_status("filled_all") is OrderStatus.FILLED
    assert from_futu_status("CANCELLED_PART") is OrderStatus.CANCELLED
    assert from_futu_status("SUBMIT_FAILED") is OrderStatus.REJECTED
    assert from_futu_status("TIMEOUT") is OrderStatus.UNKNOWN
    assert from_futu_status("SOMETHING_NEW") is OrderStatus.UNKNOWN
    assert OrderStatus.UNKNOWN.is_working  # fail closed: counts as exposure


def test_state_machine() -> None:
    assert can_transition(OrderStatus.PENDING_NEW, OrderStatus.FILLED)  # fill before ack
    assert can_transition(OrderStatus.WORKING, OrderStatus.WORKING)
    assert not can_transition(OrderStatus.FILLED, OrderStatus.WORKING)
    assert not can_transition(OrderStatus.CANCELLED, OrderStatus.FILLED)


def test_client_order_id_is_deterministic_and_short() -> None:
    a = make_client_order_id("mr", "HK.00700", "BUY", 1)
    assert a == make_client_order_id("mr", "HK.00700", "BUY", 1)
    assert a != make_client_order_id("mr", "HK.00700", "BUY", 2)
    assert len(a) == 20
    assert a.startswith("c")


def test_managed_order_remaining_qty() -> None:
    intent = OrderIntent("c1", "HK.00700", "BUY", 200, 385.2, "mr", 0, 385.3)
    order = ManagedOrder(intent=intent, status=OrderStatus.PARTIALLY_FILLED, filled_qty=100)
    assert order.remaining_qty == 100
    order.status = OrderStatus.CANCELLED
    assert order.remaining_qty == 0


def test_simulated_clock_never_goes_backwards() -> None:
    clock = SimulatedClock(100)
    clock.advance_to(50)
    assert clock.now_ns() == 100
    clock.advance_to(200)
    assert clock.monotonic_ns() == 200
