"""Execution stage."""

from __future__ import annotations

from dataclasses import dataclass

from trader.core.orders import OrderIntent
from trader.execution.order_manager import OrderManager
from trader.pipeline.base import IStage
from trader.utils.logger import get_logger

logger = get_logger("execution_stage")


@dataclass(slots=True)
class OrderReceipt:
    """Order placement receipt."""

    client_order_id: str
    order_id: str
    status: str


class ExecutionStage(IStage[OrderIntent, OrderReceipt]):
    """Send risk-approved intents through the order manager."""

    def __init__(self, order_manager: OrderManager) -> None:
        self.order_manager = order_manager

    async def process(self, item: OrderIntent) -> OrderReceipt:
        """Place order and return receipt.

        Args:
            item: Order intent approved by the risk stage.

        Returns:
            OrderReceipt with the client id, Futu order id and internal status.
        """
        logger.info(
            "order_submitting",
            symbol=item.symbol,
            side=item.side,
            qty=item.qty,
            limit_price=item.limit_price,
            reference_price=item.reference_price,
            client_order_id=item.client_order_id,
        )
        try:
            order = await self.order_manager.place(item)
        except Exception:
            logger.warning(
                "order_placement_failed",
                symbol=item.symbol,
                client_order_id=item.client_order_id,
                exc_info=True,
            )
            return OrderReceipt(client_order_id=item.client_order_id, order_id="", status="ERROR")
        receipt = OrderReceipt(
            client_order_id=item.client_order_id,
            order_id=order.broker_order_id or "",
            status=order.status.value,
        )
        logger.info(
            "order_placed",
            symbol=item.symbol,
            side=item.side,
            qty=item.qty,
            price=item.limit_price,
            client_order_id=item.client_order_id,
            order_id=receipt.order_id,
            status=receipt.status,
        )
        return receipt
