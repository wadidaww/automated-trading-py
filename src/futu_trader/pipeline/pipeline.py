"""Trading pipeline orchestration."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from prometheus_client import Counter, Gauge

from futu_trader.api.quote_handler import QuoteEvent
from futu_trader.pipeline.factory import DefaultPipelineFactory, IPipelineFactory

QUEUE_DEPTH = Gauge(name="pipeline_queue_depth", documentation="Queue depth", labelnames=["queue"])
THROUGHPUT = Counter(
    name="pipeline_items_processed_total", documentation="Processed items", labelnames=["stage"]
)


class TradingPipeline:
    """Event-driven async pipeline with start/stop/drain lifecycle."""

    def __init__(self, factory: IPipelineFactory | None = None, queue_maxsize: int = 1000) -> None:
        self.input_queue: asyncio.Queue[QuoteEvent] = asyncio.Queue(maxsize=queue_maxsize)
        self._tasks: list[asyncio.Task[None]] = []

        _factory = factory or DefaultPipelineFactory()
        self.data_stage = _factory.create_data_stage()
        self.signal_stage = _factory.create_signal_stage()
        self.risk_stage = _factory.create_risk_stage()
        self.execution_stage = _factory.create_execution_stage()
        self.audit_stage = _factory.create_audit_stage()

    async def start(self) -> None:
        """Start background worker."""
        self._tasks.append(asyncio.create_task(self._worker()))

    async def stop(self) -> None:
        """Stop worker gracefully."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def drain(self) -> None:
        """Wait until in-flight queue is drained."""
        await self.input_queue.join()

    async def submit(self, quote: QuoteEvent) -> None:
        """Submit quote event into pipeline."""
        await self.input_queue.put(quote)

    async def _worker(self) -> None:
        """Single-worker pipeline implementation."""
        while True:
            item = await self.input_queue.get()
            QUEUE_DEPTH.labels("input").set(self.input_queue.qsize())
            data = await self.data_stage.process(item)
            THROUGHPUT.labels("data").inc()
            signal = await self.signal_stage.process(data)
            if signal is not None:
                approved = await self.risk_stage.process(signal)
                if approved is not None:
                    receipt = await self.execution_stage.process(approved)
                    await self.audit_stage.process(receipt)
            self.input_queue.task_done()
