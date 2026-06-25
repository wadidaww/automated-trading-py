"""Trade handlers for order/fill updates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(slots=True)
class OrderUpdateEvent:
    """Order update event."""

    order_id: str
    status: str
    timestamp: datetime


@dataclass(slots=True)
class FillEvent:
    """Fill event."""

    order_id: str
    symbol: str
    quantity: int
    price_minor: int
    timestamp: datetime


class TradeHandler:
    """Queue-based trade update emitter."""

    def __init__(self, queue: asyncio.Queue[OrderUpdateEvent | FillEvent]) -> None:
        self.queue = queue

    async def on_order(self, order_id: str, status: str) -> None:
        """Emit order update event."""
        await self.queue.put(
            OrderUpdateEvent(order_id=order_id, status=status, timestamp=datetime.now(tz=UTC))
        )

    async def on_fill(self, order_id: str, symbol: str, quantity: int, price_minor: int) -> None:
        """Emit fill event."""
        await self.queue.put(
            FillEvent(
                order_id=order_id,
                symbol=symbol,
                quantity=quantity,
                price_minor=price_minor,
                timestamp=datetime.now(tz=UTC),
            )
        )
