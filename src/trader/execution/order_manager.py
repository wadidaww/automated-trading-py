"""Order management: idempotent sends and the order state machine."""

from __future__ import annotations

from trader.api.broker import BrokerClient
from trader.core.orders import (
    ManagedOrder,
    OrderIntent,
    OrderStatus,
    can_transition,
    from_futu_status,
)
from trader.risk.kill_switch import KillSwitch
from trader.utils.logger import get_logger

logger = get_logger("order_manager")


class DuplicateOrderError(ValueError):
    """An intent with this client order id was already sent."""


class TradingHaltedError(RuntimeError):
    """The kill switch is tripped; nothing is sent."""


class OrderManager:
    """Sends order intents at most once and tracks them by client order id.

    The client order id travels to Futu in ``remark`` and the Futu ``order_id`` is stored on the
    managed order, so broker state can be reconciled against local state.
    """

    def __init__(self, client: BrokerClient, kill_switch: KillSwitch | None = None) -> None:
        self.client = client
        self._kill_switch = kill_switch
        self._orders: dict[str, ManagedOrder] = {}

    @property
    def orders(self) -> dict[str, ManagedOrder]:
        """Managed orders keyed by client order id."""
        return self._orders

    def working_orders(self) -> list[ManagedOrder]:
        """Orders that may still fill."""
        return [order for order in self._orders.values() if order.status.is_working]

    async def place(self, intent: OrderIntent) -> ManagedOrder:
        """Send an intent as a LIMIT order, exactly once.

        The order is registered as PENDING_NEW *before* the send, so a crash or exception
        mid-send leaves it counted as working rather than forgotten. A send that raises is
        marked UNKNOWN (the broker may or may not have it) until reconciliation.

        Raises:
            DuplicateOrderError: When the intent's client order id was already sent.
            TradingHaltedError: When the kill switch is tripped (checked last, right before send).
        """
        if intent.client_order_id in self._orders:
            raise DuplicateOrderError(intent.client_order_id)
        if self._kill_switch is not None and self._kill_switch.is_tripped():
            raise TradingHaltedError(self._kill_switch.reason or "kill_switch")
        managed = ManagedOrder(intent=intent, updated_ns=intent.created_ns)
        self._orders[intent.client_order_id] = managed
        try:
            response = await self.client.place_order(
                intent.symbol,
                intent.qty,
                intent.side,
                order_type="LIMIT",
                price=intent.limit_price,
                remark=intent.client_order_id,
            )
        except Exception:
            self.apply_status(managed, OrderStatus.UNKNOWN)
            raise
        managed.broker_order_id = response.order_id
        self.apply_status(managed, from_futu_status(response.status))
        return managed

    def apply_status(self, order: ManagedOrder, status: OrderStatus) -> bool:
        """Move an order to ``status`` if the state machine allows it.

        Returns:
            bool: Whether the transition was applied.
        """
        if not can_transition(order.status, status):
            logger.warning(
                "illegal_order_transition",
                client_order_id=order.intent.client_order_id,
                current=order.status.value,
                target=status.value,
            )
            return False
        order.status = status
        return True
