"""Shared pipeline lifecycle helpers for tests."""

from __future__ import annotations

from datetime import UTC, datetime

from trader.api.quote_handler import QuoteEvent
from trader.pipeline.pipeline import TradingPipeline


async def run_pipeline(symbol: str, price: float, *, queue_maxsize: int = 10) -> TradingPipeline:
    """Run a single quote event through the pipeline and return the stopped pipeline."""
    pipeline = TradingPipeline(queue_maxsize=queue_maxsize)
    await pipeline.start()
    await pipeline.submit(QuoteEvent(symbol=symbol, price=price, timestamp=datetime.now(tz=UTC)))
    await pipeline.drain()
    await pipeline.stop()
    return pipeline
