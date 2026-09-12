"""Audit stage."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict

from trader.pipeline.base import IStage
from trader.pipeline.execution_stage import OrderReceipt
from trader.utils.logger import get_logger

_MAX_EVENTS = 1000


class AuditStage(IStage[OrderReceipt, None]):
    """Persist/audit receipts with bounded buffer."""

    def __init__(self, max_events: int = _MAX_EVENTS) -> None:
        self.logger = get_logger("audit")
        self.events: deque[dict[str, str]] = deque(maxlen=max_events)

    async def process(self, item: OrderReceipt) -> None:
        """Log and store receipt.

        Args:
            item: Order placement receipt.
        """
        payload = asdict(item)
        self.events.append(payload)
        self.logger.info("order_receipt", **payload)
