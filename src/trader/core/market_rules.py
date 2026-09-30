"""Exchange microstructure rules: tick ladders, board lots and trading sessions.

Prices are handled in integer *milli-units* internally (HK's minimum tick is 0.001) so that tick
alignment is exact and free of float drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Final

from trader.core.orders import Side
from trader.core.symbols import market_of

_MILLI: Final[int] = 1000
# Rounding works on micro-units: finer than every tick in every supported market.
_MICRO: Final[int] = 1_000_000

# HKEX spread table (Part A), as (upper bound exclusive in milli-units, tick in milli-units).
_HK_TICKS: Final[tuple[tuple[int, int], ...]] = (
    (250, 1),
    (500, 5),
    (10_000, 10),
    (20_000, 20),
    (100_000, 50),
    (200_000, 100),
    (500_000, 200),
    (1_000_000, 500),
    (2_000_000, 1_000),
    (5_000_000, 2_000),
    (10_000_000, 5_000),
)


def _to_milli(price: float) -> int:
    return round(price * _MILLI)


def tick_size(symbol: str, price: float) -> float:
    """Minimum price increment for ``symbol`` at ``price``.

    HK uses the HKEX spread table; US uses 0.01 at or above $1 and 0.0001 below; other markets
    default to 0.01.
    """
    market = market_of(symbol)
    if market == "HK":
        milli = _to_milli(price)
        for upper, tick in _HK_TICKS:
            if milli < upper:
                return tick / _MILLI
        return _HK_TICKS[-1][1] / _MILLI
    if market == "US":
        return 0.01 if price >= 1.0 else 0.0001
    return 0.01


def round_to_tick(symbol: str, price: float, side: Side) -> float:
    """Round a limit price onto the tick ladder, *passively* (BUY down, SELL up).

    Passive rounding never makes an order more aggressive than the strategy intended.
    """
    tick = tick_size(symbol, price)
    p = round(price * _MICRO)
    t = round(tick * _MICRO)
    steps = p // t if side == "BUY" else -((-p) // t)
    return steps * t / _MICRO


def is_on_tick(symbol: str, price: float) -> bool:
    """Whether ``price`` lies exactly on the tick ladder."""
    return round_to_tick(symbol, price, "BUY") == round_to_tick(symbol, price, "SELL")


def round_down_to_lot(qty: int, lot_size: int) -> int:
    """Largest whole-lot quantity ≤ ``qty``. Returns 0 when ``lot_size`` is not positive."""
    if lot_size <= 0 or qty <= 0:
        return 0
    return (qty // lot_size) * lot_size


class SessionPhase(Enum):
    """Coarse trading-session phase used to gate order types."""

    CLOSED = "CLOSED"
    PRE_OPEN = "PRE_OPEN"  # HK 09:00-09:30 auction
    CONTINUOUS = "CONTINUOUS"
    LUNCH = "LUNCH"
    CLOSING_AUCTION = "CLOSING_AUCTION"  # HK CAS 16:00-16:10


HKT: Final[timezone] = timezone(timedelta(hours=8), "HKT")


@dataclass(slots=True)
class HKSessionCalendar:
    """HKEX securities-market sessions (HKT). Holidays are injected from config.

    Continuous trading: 09:30-12:00 and 13:00-16:00. Half days (Christmas/New Year/Lunar New
    Year eves) close at 12:00 and are passed via ``half_days``.
    """

    holidays: frozenset[date] = frozenset()
    half_days: frozenset[date] = frozenset()

    def phase(self, ts_ns: int) -> SessionPhase:
        """Session phase at UTC epoch ``ts_ns``."""
        local = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC).astimezone(HKT)
        day, now = local.date(), local.time()
        if local.weekday() >= 5 or day in self.holidays:
            return SessionPhase.CLOSED
        if time(9, 0) <= now < time(9, 30):
            return SessionPhase.PRE_OPEN
        if time(9, 30) <= now < time(12, 0):
            return SessionPhase.CONTINUOUS
        if day in self.half_days:
            return SessionPhase.CLOSED
        if time(12, 0) <= now < time(13, 0):
            return SessionPhase.LUNCH
        if time(13, 0) <= now < time(16, 0):
            return SessionPhase.CONTINUOUS
        if time(16, 0) <= now < time(16, 10):
            return SessionPhase.CLOSING_AUCTION
        return SessionPhase.CLOSED

    def is_continuous(self, ts_ns: int) -> bool:
        """Whether continuous-session limit orders may be sent at ``ts_ns``."""
        return self.phase(ts_ns) is SessionPhase.CONTINUOUS
