"""Execution stage."""

from __future__ import annotations

from dataclasses import dataclass

from trader.execution.order_manager import OrderManager
from trader.pipeline.base import IStage
from trader.pipeline.risk_stage import ApprovedOrder
from trader.utils.logger import get_logger

logger = get_logger("execution_stage")


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
        """Place order and return receipt.

        Args:
            item: Approved order from risk stage.

        Returns:
            OrderReceipt with order ID and status.
        """
        try:
            order = await self.order_manager.place_order(
                item.symbol,
                item.qty,
                item.side,
                price=item.price,
                order_type="LIMIT",
            )
            receipt = OrderReceipt(order_id=order.dedupe_key, status=order.state.value)
            logger.info(
                "order_placed",
                symbol=item.symbol,
                side=item.side,
                qty=item.qty,
                price=item.price,
                order_id=receipt.order_id,
                status=receipt.status,
            )
            return receipt
        except Exception:
            logger.warning("order_placement_failed", symbol=item.symbol, exc_info=True)
            return OrderReceipt(order_id="", status="ERROR")
