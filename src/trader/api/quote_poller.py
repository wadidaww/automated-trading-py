"""Periodic quote polling loop."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from trader.api.client import FutuClient
from trader.api.quote_handler import QuoteHandler
from trader.utils.logger import get_logger

logger = get_logger("quote_poller")


class QuotePoller:
    """Background task that polls Futu for quotes and feeds them to the pipeline.

    Fetches the latest quote for each configured symbol at a fixed interval
    and pushes QuoteEvents into the pipeline via QuoteHandler.
    """

    def __init__(
        self,
        client: FutuClient,
        handler: QuoteHandler,
        symbols: list[str],
        interval_s: float = 60.0,
    ) -> None:
        self._client = client
        self._handler = handler
        self._symbols = symbols
        self._interval_s = max(interval_s, 1.0)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background polling loop."""
        self._task = asyncio.create_task(self._loop())
        logger.info("quote_poller_started", symbols=self._symbols, interval_s=self._interval_s)

    async def stop(self) -> None:
        """Stop the polling loop gracefully."""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("quote_poller_stopped")

    async def _loop(self) -> None:
        """Poll quotes at fixed interval until cancelled."""
        while True:
            for symbol in self._symbols:
                try:
                    quote = await self._client.get_quote(symbol)
                    await self._handler.on_quote(quote.symbol, quote.price)
                except Exception:
                    logger.warning("quote_fetch_failed", symbol=symbol, exc_info=True)
            await asyncio.sleep(self._interval_s)
