"""Data pipeline stage."""

from __future__ import annotations

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
    """Convert raw quotes to lightweight feature windows."""

    def __init__(self, mean: float = 0.0, std_dev: float = 0.0) -> None:
        self.mean = mean
        self.std_dev = std_dev

    async def process(self, item: QuoteEvent) -> FeatureWindow:
        """Process input event."""
        z_score = MathFormula.calc_z_score(item.price, self.mean, self.std_dev)
        return FeatureWindow(symbol=item.symbol, price=item.price, z_score=z_score)
