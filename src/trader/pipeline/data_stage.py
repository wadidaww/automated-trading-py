"""Data pipeline stage."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from trader.api.quote_handler import QuoteEvent
from trader.pipeline.base import IStage
from trader.utils.maths import MathFormula


@dataclass(slots=True)
class FeatureWindow:
    """Features prepared for model prediction."""

    symbol: str
    price: float
    z_score: float
    momentum: float = 0.0
    trend_strength: float = 0.0
    volatility: float = 0.0
    rsi: float = 50.0
    bb_position: float = 0.5
    price_acceleration: float = 0.0


class DataStage(IStage[QuoteEvent, FeatureWindow]):
    """Convert raw quotes to rich feature windows.

    Maintains a per-symbol rolling window of prices and computes
    multiple technical features for signal generation:
    - z_score: deviation from rolling mean
    - momentum: rate of price change
    trend_strength: EMA-based trend direction
    - volatility: rolling price volatility
    - rsi: relative strength index
    - bb_position: position within Bollinger Bands
    - price_acceleration: second derivative of price
    """

    def __init__(self, window_size: int = 100) -> None:
        self._window_size = window_size
        self._prices: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window_size))
        self._prev_prices: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=10))

    async def process(self, item: QuoteEvent) -> FeatureWindow:
        """Process input event with enriched feature computation.

        Args:
            item: Incoming quote event.

        Returns:
            FeatureWindow with multiple computed features.
        """
        prices = self._prices[item.symbol]
        prev_prices = self._prev_prices[item.symbol]
        prices.append(item.price)

        if len(prices) < 5:
            return FeatureWindow(symbol=item.symbol, price=item.price, z_score=0.0)

        price_list = list(prices)
        mean = MathFormula.calc_mean(price_list)
        std_dev = MathFormula.calc_std_dev(price_list)
        z_score = MathFormula.calc_z_score(item.price, mean, std_dev) if std_dev > 0 else 0.0

        momentum = self._compute_momentum(price_list)
        trend_strength = self._compute_trend_strength(price_list)
        volatility = self._compute_volatility(price_list)
        rsi = self._compute_rsi(price_list)
        bb_position = self._compute_bb_position(price_list, mean, std_dev)
        price_acceleration = self._compute_acceleration(prev_prices, item.price)
        prev_prices.append(item.price)

        return FeatureWindow(
            symbol=item.symbol,
            price=item.price,
            z_score=z_score,
            momentum=momentum,
            trend_strength=trend_strength,
            volatility=volatility,
            rsi=rsi,
            bb_position=bb_position,
            price_acceleration=price_acceleration,
        )

    @staticmethod
    def _compute_momentum(prices: list[float]) -> float:
        """Compute rate of price change over recent window."""
        if len(prices) < 5:
            return 0.0
        recent = prices[-5]
        if recent == 0:
            return 0.0
        return (prices[-1] - recent) / recent

    @staticmethod
    def _compute_trend_strength(prices: list[float]) -> float:
        """Compute EMA-based trend direction and strength.

        Returns positive for uptrend, negative for downtrend.
        """
        if len(prices) < 10:
            return 0.0
        ema_short = _ema(prices, 5)
        ema_long = _ema(prices, 10)
        if ema_long == 0:
            return 0.0
        return (ema_short - ema_long) / ema_long

    @staticmethod
    def _compute_volatility(prices: list[float]) -> float:
        """Compute rolling volatility (annualized)."""
        if len(prices) < 10:
            return 0.0
        returns = [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        return variance**0.5

    @staticmethod
    def _compute_rsi(prices: list[float], period: int = 14) -> float:
        """Compute Relative Strength Index."""
        if len(prices) < period + 1:
            return 50.0
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent = deltas[-period:]
        avg_gain = sum(d for d in recent if d > 0) / period
        total_loss = sum(-d for d in recent if d < 0)
        avg_loss = total_loss / period if total_loss else 0.0001
        return 100 - 100 / (1 + avg_gain / avg_loss)

    @staticmethod
    def _compute_bb_position(prices: list[float], mean: float, std_dev: float) -> float:
        """Compute position within Bollinger Bands (0 = lower, 1 = upper)."""
        if std_dev == 0 or len(prices) < 20:
            return 0.5
        upper = mean + 2 * std_dev
        lower = mean - 2 * std_dev
        band_width = upper - lower
        if band_width == 0:
            return 0.5
        return (prices[-1] - lower) / band_width

    @staticmethod
    def _compute_acceleration(prev_prices: deque[float], current_price: float) -> float:
        """Compute second derivative of price (acceleration/deceleration)."""
        if len(prev_prices) < 2:
            return 0.0
        prev_list = list(prev_prices)
        velocity_1 = current_price - prev_list[-1]
        velocity_0 = prev_list[-1] - prev_list[-2]
        return velocity_1 - velocity_0


def _ema(prices: list[float], span: int) -> float:
    """Compute exponential moving average."""
    if not prices:
        return 0.0
    multiplier = 2 / (span + 1)
    ema_val = prices[0]
    for price in prices[1:]:
        ema_val = (price - ema_val) * multiplier + ema_val
    return ema_val
