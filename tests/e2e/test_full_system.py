from __future__ import annotations

from datetime import UTC, datetime

import pytest

from futu_trader.api.quote_handler import QuoteEvent
from futu_trader.pipeline.pipeline import TradingPipeline


@pytest.mark.asyncio
async def test_full_system_paper_flow() -> None:
    pipeline = TradingPipeline(queue_maxsize=10)
    await pipeline.start()
    await pipeline.submit(QuoteEvent(symbol="3690.HK", price=200.0, timestamp=datetime.now(tz=UTC)))
    await pipeline.drain()
    await pipeline.stop()
    assert len(pipeline.audit_stage.events) == 1
