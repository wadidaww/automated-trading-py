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


class DataStage(IStage[QuoteEvent, FeatureWindow]):
    """Convert raw quotes to lightweight feature windows.

    Maintains a per-symbol rolling window of prices and computes z-score
    from the actual rolling statistics rather than static values.
    """

    def __init__(self, window_size: int = 100) -> None:
        self._window_size = window_size
        self._prices: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window_size))

    async def process(self, item: QuoteEvent) -> FeatureWindow:
        """Process input event.

        Appends the price to the rolling window and computes z-score
        from the actual mean and standard deviation of recent prices.

        Args:
            item: Incoming quote event.

        Returns:
            FeatureWindow with computed z-score.
        """
        prices = self._prices[item.symbol]
        prices.append(item.price)

        if len(prices) < 2:
            return FeatureWindow(symbol=item.symbol, price=item.price, z_score=0.0)

        price_list = list(prices)
        mean = MathFormula.calc_mean(price_list)
        std_dev = MathFormula.calc_std_dev(price_list)

        if std_dev == 0:
            return FeatureWindow(symbol=item.symbol, price=item.price, z_score=0.0)

        z_score = MathFormula.calc_z_score(item.price, mean, std_dev)
        return FeatureWindow(symbol=item.symbol, price=item.price, z_score=z_score)
