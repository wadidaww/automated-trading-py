"""Execution stage."""

from __future__ import annotations

from dataclasses import dataclass

from trader.execution.order_manager import OrderManager
from trader.pipeline.base import IStage
from trader.pipeline.risk_stage import ApprovedOrder


@dataclass(slots=True)
class OrderReceipt:
    """Order placement receipt."""

    order_id: str
    status: str


class ExecutionStage(IStage[ApprovedOrder, OrderReceipt]):
    """Place approved orders using order manager."""

    def __init__(self, order_manager: OrderManager) -> None:
        self.order_manager = order_manager

    async def process(self, item: ApprovedOrder) -> OrderReceipt:
        """Place order and return receipt."""
        order = await self.order_manager.place_order(item.symbol, item.qty, item.side)
        return OrderReceipt(order_id=order.dedupe_key, status=order.state.value)
