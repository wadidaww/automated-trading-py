from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trader.api.quote_handler import QuoteEvent
from trader.pipeline.pipeline import TradingPipeline


@pytest.mark.asyncio
async def test_pipeline_end_to_end() -> None:
    pipeline = TradingPipeline(queue_maxsize=10)
    await pipeline.start()
    await pipeline.submit(QuoteEvent(symbol="9988.HK", price=88.0, timestamp=datetime.now(tz=UTC)))
    await pipeline.drain()
    await pipeline.stop()
    assert pipeline.audit_stage.events
