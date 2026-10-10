"""Data pipeline stage."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from trader.api.quote_handler import QuoteEvent
from trader.data import indicators
from trader.pipeline.base import IStage
from trader.utils.logger import get_logger
from trader.utils.maths import mean, std_dev, z_score

logger = get_logger("data_stage")

# Fewer points than this and the window is too thin for any feature.
_MIN_POINTS = 5
_ACCELERATION_HISTORY = 10


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
    """Convert raw quotes to feature windows over a per-symbol rolling price window.

    Features are the pure functions in ``trader.data.indicators`` plus the z-score of the latest
    price against the window.
    """

    def __init__(self, window_size: int = 100) -> None:
        self._window_size = window_size
        self._prices: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window_size))
        self._prev_prices: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=_ACCELERATION_HISTORY)
        )

    async def process(self, item: QuoteEvent) -> FeatureWindow:
        """Add the quote to its window and compute the features."""
        window = self._prices[item.symbol]
        previous = self._prev_prices[item.symbol]
        window.append(item.price)

        if len(window) < _MIN_POINTS:
            logger.info(
                "quote_received",
                symbol=item.symbol,
                price=item.price,
                window_points=len(window),
                warming_up=True,
            )
            return FeatureWindow(symbol=item.symbol, price=item.price, z_score=0.0)

        prices = list(window)
        centre = mean(prices)
        spread = std_dev(prices)
        features = FeatureWindow(
            symbol=item.symbol,
            price=item.price,
            z_score=z_score(item.price, centre, spread) if spread > 0 else 0.0,
            momentum=indicators.momentum(prices),
            trend_strength=indicators.trend_strength(prices),
            volatility=indicators.volatility(prices),
            rsi=indicators.rsi(prices),
            bb_position=indicators.bollinger_position(prices, centre, spread),
            price_acceleration=indicators.acceleration(previous, item.price),
        )
        previous.append(item.price)
        logger.info(
            "features_computed",
            symbol=item.symbol,
            price=item.price,
            window_points=len(prices),
            window_mean=round(centre, 6),
            window_std=round(spread, 6),
            z_score=round(features.z_score, 4),
            momentum=round(features.momentum, 4),
            trend_strength=round(features.trend_strength, 4),
            volatility=round(features.volatility, 4),
            rsi=round(features.rsi, 2),
            bb_position=round(features.bb_position, 4),
            price_acceleration=round(features.price_acceleration, 4),
        )
        return features
