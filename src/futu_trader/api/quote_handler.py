"""Quote handlers that transform stream callbacks into queue events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(slots=True)
class QuoteEvent:
    """Market quote event."""

    symbol: str
    price: float
    timestamp: datetime


class QuoteHandler:
    """Queue-based quote event emitter."""

    def __init__(self, queue: asyncio.Queue[QuoteEvent]) -> None:
        self.queue = queue

    async def on_quote(self, symbol: str, price: float) -> None:
        """Emit quote event.

        Args:
            symbol: Symbol code.
            price: Last traded price.
        """
        await self.queue.put(QuoteEvent(symbol=symbol, price=price, timestamp=datetime.now(tz=UTC)))
