"""Exchange microstructure rules: tick ladders, board lots and trading sessions.

Prices are handled in integer *milli-units* internally (HK's minimum tick is 0.001) so that tick
alignment is exact and free of float drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from datetime import UTC, date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Final

from trader.core.orders import Side
from trader.core.symbols import CRYPTO_MARKET, market_of

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


@dataclass(frozen=True, slots=True)
class MarketRules:
    """Per-market order conventions the risk stage branches on.

    Attributes:
        fractional_qty: Quantities are decimal amounts on a step (crypto), not whole board lots.
        broker_tick: The tick has no static ladder and must come from the broker's instrument
            data; without it the order is rejected rather than guessed.
    """

    fractional_qty: bool = False
    broker_tick: bool = False


_DEFAULT_RULES: Final[MarketRules] = MarketRules()
MARKET_RULES: Final[dict[str, MarketRules]] = {
    CRYPTO_MARKET: MarketRules(fractional_qty=True, broker_tick=True),
}


def rules_for(symbol: str) -> MarketRules:
    """Order conventions for the market of ``symbol`` (equity defaults for unlisted markets)."""
    return MARKET_RULES.get(market_of(symbol), _DEFAULT_RULES)


def tick_size(symbol: str, price: float, tick: float | None = None) -> float:
    """Minimum price increment for ``symbol`` at ``price``.

    HK uses the HKEX spread table; US uses 0.01 at or above $1 and 0.0001 below; other markets
    default to 0.01. Markets whose tick is broker-defined (crypto) use the explicit ``tick``.

    Args:
        symbol: Futu code.
        price: Reference price.
        tick: Broker-supplied tick; required for markets with ``broker_tick`` rules.

    Raises:
        ValueError: When the market needs a broker tick and none (or a non-positive one) is given.
    """
    if tick is not None and tick > 0:
        return tick
    if rules_for(symbol).broker_tick:
        raise ValueError(f"{symbol} needs a broker-supplied tick size")
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


def round_to_tick(symbol: str, price: float, side: Side, tick: float | None = None) -> float:
    """Round a limit price onto the tick ladder, *passively* (BUY down, SELL up).

    Passive rounding never makes an order more aggressive than the strategy intended. Crypto
    ticks are finer than a micro-unit grid can hold exactly, so that path uses ``Decimal``.
    """
    step = tick_size(symbol, price, tick)
    if rules_for(symbol).fractional_qty:
        return _round_decimal(price, step, ROUND_FLOOR if side == "BUY" else ROUND_CEILING)
    p = round(price * _MICRO)
    t = round(step * _MICRO)
    steps = p // t if side == "BUY" else -((-p) // t)
    return steps * t / _MICRO


def _round_decimal(value: float, step: float, rounding: str) -> float:
    """Round ``value`` to a multiple of ``step`` in exact decimal arithmetic."""
    quantum = Decimal(str(step))
    steps = (Decimal(str(value)) / quantum).to_integral_value(rounding=rounding)
    return float(steps * quantum)


def is_on_tick(symbol: str, price: float, tick: float | None = None) -> bool:
    """Whether ``price`` lies exactly on the tick ladder."""
    return round_to_tick(symbol, price, "BUY", tick) == round_to_tick(symbol, price, "SELL", tick)


def round_down_to_lot(qty: float, lot_size: int) -> int:
    """Largest whole-lot quantity ≤ ``qty``. Returns 0 when ``lot_size`` is not positive."""
    if lot_size <= 0 or qty <= 0:
        return 0
    return (int(qty) // lot_size) * lot_size


def round_down_to_step(qty: float, step: float) -> float:
    """Largest multiple of ``step`` ≤ ``qty`` (crypto). Returns 0 when ``step`` is not positive."""
    if step <= 0 or qty <= 0:
        return 0.0
    return _round_decimal(qty, step, ROUND_FLOOR)


class SessionPhase(Enum):
    """Coarse trading-session phase used to gate order types."""

    CLOSED = "CLOSED"
    PRE_OPEN = "PRE_OPEN"  # HK 09:00-09:30 auction
    CONTINUOUS = "CONTINUOUS"
    LUNCH = "LUNCH"
    CLOSING_AUCTION = "CLOSING_AUCTION"  # HK CAS 16:00-16:10


HKT: Final[timezone] = timezone(timedelta(hours=8), "HKT")


@dataclass(slots=True)
class CryptoSessionCalendar:
    """Crypto trades around the clock: every instant is a continuous session."""

    def phase(self, ts_ns: int) -> SessionPhase:
        """Always ``CONTINUOUS``; ``ts_ns`` is accepted for calendar-interface parity."""
        del ts_ns
        return SessionPhase.CONTINUOUS

    def is_continuous(self, ts_ns: int) -> bool:
        """Always True."""
        return self.phase(ts_ns) is SessionPhase.CONTINUOUS


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
