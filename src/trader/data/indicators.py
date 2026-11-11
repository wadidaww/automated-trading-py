"""Pure technical indicators over a price window (oldest first)."""

from __future__ import annotations

import math
from collections.abc import Sequence


def momentum(prices: Sequence[float], lookback: int = 5) -> float:
    """Fractional price change over the last ``lookback`` points."""
    if len(prices) < lookback:
        return 0.0
    base = prices[-lookback]
    return (prices[-1] - base) / base if base else 0.0


def ema(prices: Sequence[float], span: int) -> float:
    """Exponential moving average seeded with the first price."""
    if not prices:
        return 0.0
    alpha = 2 / (span + 1)
    value = prices[0]
    for price in prices[1:]:
        value += (price - value) * alpha
    return value


def trend_strength(prices: Sequence[float]) -> float:
    """Relative gap between the 5- and 10-period EMA: positive uptrend, negative downtrend."""
    if len(prices) < 10:
        return 0.0
    slow = ema(prices, 10)
    return (ema(prices, 5) - slow) / slow if slow else 0.0


def volatility(prices: Sequence[float]) -> float:
    """Sample standard deviation of simple returns."""
    if len(prices) < 10:
        return 0.0
    returns = [curr / prev - 1 for prev, curr in zip(prices, prices[1:], strict=False)]
    centre = sum(returns) / len(returns)
    return (sum((r - centre) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5


def rsi(prices: Sequence[float], period: int = 14) -> float:
    """Relative Strength Index; neutral 50 until ``period + 1`` points exist."""
    if len(prices) < period + 1:
        return 50.0
    deltas = [curr - prev for prev, curr in zip(prices, prices[1:], strict=False)][-period:]
    avg_gain = sum(d for d in deltas if d > 0) / period
    total_loss = sum(-d for d in deltas if d < 0)
    avg_loss = total_loss / period if total_loss else 0.0001
    return 100 - 100 / (1 + avg_gain / avg_loss)


def bollinger_position(prices: Sequence[float], centre: float, spread: float) -> float:
    """Where the last price sits in the ±2σ band (0 = lower, 1 = upper); 0.5 when undefined."""
    if spread == 0 or len(prices) < 20:
        return 0.5
    lower = centre - 2 * spread
    return (prices[-1] - lower) / (4 * spread)


def acceleration(previous: Sequence[float], current: float) -> float:
    """Second difference of price from the last two previous points and the current one."""
    if len(previous) < 2:
        return 0.0
    return (current - previous[-1]) - (previous[-1] - previous[-2])


def blended_return(prices: Sequence[float], lookbacks: Sequence[int]) -> float:
    """Mean of the simple returns over each lookback (in bars); NaN until the longest one exists."""
    if not lookbacks or len(prices) <= max(lookbacks):
        return math.nan
    total = 0.0
    for lookback in lookbacks:
        base = prices[-1 - lookback]
        if base <= 0:
            return math.nan
        total += prices[-1] / base - 1
    return total / len(lookbacks)


def sma_gap(prices: Sequence[float], window: int) -> float:
    """Last price over the mean of the last ``window`` prices, minus 1.

    NaN until ``window`` prices exist, and NaN when any of them is non-finite or not positive: a
    bad print must give no signal, never an exit or an entry.
    """
    if window <= 0 or len(prices) < window:
        return math.nan
    tail = prices[-window:]
    if any(not math.isfinite(price) or price <= 0 for price in tail):
        return math.nan
    return prices[-1] / (sum(tail) / window) - 1


def donchian_long(prices: Sequence[float], entry: int, exit_: int) -> float:
    """Donchian channel state replayed over the window: 1.0 long, 0.0 flat, NaN if too short.

    Goes long when a close exceeds the highest of the previous ``entry`` closes and flat when a
    close falls below the lowest of the previous ``exit_`` closes. The replay starts flat at the
    first bar that has ``entry`` closes before it, so the window must be much longer than
    ``entry`` for the state to match a replay over the full history.
    """
    if entry <= 0 or exit_ <= 0 or len(prices) <= max(entry, exit_):
        return math.nan
    long = False
    for i in range(max(entry, exit_), len(prices)):
        price = prices[i]
        if not long and price > max(prices[i - entry : i]):
            long = True
        elif long and price < min(prices[i - exit_ : i]):
            long = False
    return 1.0 if long else 0.0


def realised_vol(prices: Sequence[float], window: int, bars_per_year: float) -> float:
    """Annualised sample standard deviation of the last ``window`` simple returns; NaN if short."""
    if window < 2 or len(prices) < window + 1:
        return math.nan
    tail = prices[-window - 1 :]
    if any(price <= 0 for price in tail):
        return math.nan
    returns = [curr / prev - 1 for prev, curr in zip(tail, tail[1:], strict=False)]
    centre = sum(returns) / window
    variance = sum((r - centre) ** 2 for r in returns) / (window - 1)
    return math.sqrt(variance * bars_per_year)
