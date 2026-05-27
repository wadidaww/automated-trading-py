"""Trading pipeline orchestration."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from prometheus_client import Counter, Gauge

from futu_trader.api.client import FutuClient
from futu_trader.api.quote_handler import QuoteEvent
from futu_trader.execution.order_manager import OrderManager
from futu_trader.model.mean_reversion import MeanReversionModel
from futu_trader.pipeline.audit_stage import AuditStage
from futu_trader.pipeline.data_stage import DataStage
from futu_trader.pipeline.execution_stage import ExecutionStage
from futu_trader.pipeline.risk_stage import RiskStage
from futu_trader.pipeline.signal_stage import SignalStage
from futu_trader.risk.risk_engine import RiskEngine

QUEUE_DEPTH = Gauge("pipeline_queue_depth", "Queue depth", ["queue"])
THROUGHPUT = Counter("pipeline_items_processed_total", "Processed items", ["stage"])


class TradingPipeline:
    """Event-driven async pipeline with start/stop/drain lifecycle."""

    def __init__(self, queue_maxsize: int = 1000) -> None:
        self.input_queue: asyncio.Queue[QuoteEvent] = asyncio.Queue(maxsize=queue_maxsize)
        self._tasks: list[asyncio.Task[None]] = []

        self.data_stage = DataStage()
        self.signal_stage = SignalStage(MeanReversionModel(), confidence_threshold=0.0)
        self.risk_stage = RiskStage(
            RiskEngine(
                max_symbol_notional_minor=10_000_000,
                max_portfolio_notional_minor=50_000_000,
                max_daily_loss_minor=1_000_000,
                max_open_orders=50,
                concentration_limit_pct=0.5,
            )
        )
        self.execution_stage = ExecutionStage(OrderManager(FutuClient()))
        self.audit_stage = AuditStage()

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
