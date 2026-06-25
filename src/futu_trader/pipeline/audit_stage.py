"""Audit stage."""

from __future__ import annotations

from dataclasses import asdict

from futu_trader.pipeline.execution_stage import OrderReceipt
from futu_trader.utils.logger import get_logger


class AuditStage:
    """Persist/audit receipts."""

    def __init__(self) -> None:
        self.logger = get_logger("audit")
        self.events: list[dict[str, str]] = []

    async def process(self, item: OrderReceipt) -> None:
        """Log and store receipt."""
        payload = asdict(item)
        self.events.append(payload)
        self.logger.info("order_receipt", **payload)
