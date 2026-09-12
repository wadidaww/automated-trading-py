"""Order management state machine."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

from trader.api.client import FutuClient
from trader.misc.types.futu import TradeSide


class OrderState(Enum):
    """Allowed order states."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(slots=True)
class ManagedOrder:
    """Managed order payload."""

    symbol: str
    qty: int
    side: TradeSide
    dedupe_key: str
    state: OrderState = OrderState.PENDING


class OrderManager:
    """Simple order manager with in-memory idempotency."""

    def __init__(self, client: FutuClient) -> None:
        self.client = client
        self._seen_keys: set[str] = set()

    async def place_order(
        self,
        symbol: str,
        qty: int,
        side: TradeSide,
        dedupe_key: str | None = None,
        price: float | None = None,
        order_type: str = "MARKET",
    ) -> ManagedOrder:
        """Place order if dedupe key is unseen.

        Args:
            symbol: Security code.
            qty: Quantity in shares.
            side: BUY or SELL.
            dedupe_key: Optional idempotency key.
            price: Limit price (required for LIMIT orders).
            order_type: MARKET or LIMIT.

        Returns:
            ManagedOrder with updated state.

        Raises:
            ValueError: If dedupe_key was already used.
        """
        key = dedupe_key or str(uuid.uuid4())
        if key in self._seen_keys:
            raise ValueError("duplicate order")
        self._seen_keys.add(key)
        managed = ManagedOrder(
            symbol=symbol, qty=qty, side=side, dedupe_key=key, state=OrderState.PENDING
        )
        response = await self.client.place_order(
            symbol, qty, side, order_type=order_type, price=price
        )
        managed.state = (
            OrderState.SUBMITTED if response.status == "SUBMITTED" else OrderState.REJECTED
        )
        return managed

    def transition(self, order: ManagedOrder, next_state: OrderState) -> ManagedOrder:
        """Transition order state.

        Args:
            order: Order to transition.
            next_state: Target state.

        Returns:
            Updated order.
        """
        order.state = next_state
        return order
