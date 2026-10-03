"""Pure technical indicators over a price window (oldest first)."""

from __future__ import annotations

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
