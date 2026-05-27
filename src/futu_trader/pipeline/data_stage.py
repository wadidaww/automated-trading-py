"""Data pipeline stage."""

from __future__ import annotations

from dataclasses import dataclass

from futu_trader.api.quote_handler import QuoteEvent


@dataclass(slots=True)
class FeatureWindow:
    """Features prepared for model prediction."""

    symbol: str
    price: float


class DataStage:
    """Convert raw quotes to lightweight feature windows."""

    async def process(self, item: QuoteEvent) -> FeatureWindow:
        """Process input event."""
        return FeatureWindow(symbol=item.symbol, price=item.price)
