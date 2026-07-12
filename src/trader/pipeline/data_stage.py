"""Data pipeline stage."""

from __future__ import annotations

from dataclasses import dataclass

from trader.api.quote_handler import QuoteEvent
from trader.pipeline.base import IStage


@dataclass(slots=True)
class FeatureWindow:
    """Features prepared for model prediction."""

    symbol: str
    price: float
    z_score: float


class DataStage(IStage[QuoteEvent, FeatureWindow]):
    """Convert raw quotes to lightweight feature windows."""

    async def process(self, item: QuoteEvent) -> FeatureWindow:
        """Process input event."""
        z_score = (item.price - 100.0) / 10.0
        return FeatureWindow(symbol=item.symbol, price=item.price, z_score=z_score)
