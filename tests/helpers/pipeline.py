"""Shared pipeline lifecycle helpers for tests."""

from __future__ import annotations

from datetime import UTC, datetime

from trader.api.client import FutuClient
from trader.api.quote_handler import QuoteEvent
from trader.api.simulator import PaperSimulator
from trader.pipeline.pipeline import TradingPipeline

# Oscillate +/-0.5% around the base price to fill the rolling window, then dip 1.5%:
# deep enough for a mean-reversion BUY, shallow enough to keep RSI above the model's
# oversold filter, so the quote flows through risk, execution and audit.
_WARMUP_OFFSETS_PCT = (0.005, -0.005) * 15
_DIP_PCT = 0.015


class _PinnedSimulator(PaperSimulator):
    """Simulator whose snapshot price is pinned, so the risk price band is stable."""

    def __init__(self, price: float) -> None:
        super().__init__()
        self._pinned_price = price

    def advance_price(self, symbol: str) -> float:
        return self._pinned_price


def _static_price_client(price: float) -> FutuClient:
    """Paper-fallback client whose snapshot price is pinned."""
    client = FutuClient()
    client._simulator = _PinnedSimulator(price)
    return client


async def run_pipeline(symbol: str, price: float, *, queue_maxsize: int = 64) -> TradingPipeline:
    """Run a warm-up series plus a mean-reversion dip through the pipeline."""
    dip_price = price * (1 - _DIP_PCT)
    pipeline = TradingPipeline(client=_static_price_client(dip_price), queue_maxsize=queue_maxsize)
    await pipeline.start()
    prices = [price * (1 + pct) for pct in _WARMUP_OFFSETS_PCT] + [dip_price]
    for px in prices:
        await pipeline.submit(QuoteEvent(symbol=symbol, price=px, timestamp=datetime.now(tz=UTC)))
    await pipeline.drain()
    await pipeline.stop()
    return pipeline
