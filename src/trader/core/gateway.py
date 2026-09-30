"""Broker gateway contract shared by the live Futu gateway and the simulated (backtest/paper) one.

The OMS talks only to ``BrokerGateway``. Implementations:
- ``trader.api.futu_gateway.FutuGateway`` — OpenD via futu-api (SIMULATE or REAL).
- ``trader.evaluation.sim_gateway.SimulatedGateway`` — deterministic fills for backtest/offline.

Callbacks registered with ``set_listeners`` are always invoked **on the event-loop thread**
(implementations that receive SDK pushes on other threads must hop via
``loop.call_soon_threadsafe``). Callbacks must not block.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from trader.core.orders import Fill, OrderIntent, OrderStatus, Side


@dataclass(slots=True, frozen=True)
class OrderUpdate:
    """Broker-side order state change (Futu ``TradeOrderHandlerBase`` push or query row)."""

    broker_order_id: str
    client_order_id: str  # Futu ``remark``; empty for orders not placed by this system
    symbol: str
    side: Side
    status: OrderStatus
    qty: int
    filled_qty: int
    avg_fill_price: float
    limit_price: float
    ts_ns: int
    raw_status: str = ""
    reason: str = ""


@dataclass(slots=True, frozen=True)
class PositionSnapshot:
    """Broker position row."""

    symbol: str
    qty: int
    can_sell_qty: int
    avg_cost: float
    market_value: float


@dataclass(slots=True, frozen=True)
class AccountSnapshot:
    """Broker account funds."""

    acc_id: int
    total_assets: float
    cash: float
    buying_power: float
    market_value: float
    currency: str


@dataclass(slots=True, frozen=True)
class InstrumentInfo:
    """Static per-symbol reference data needed for order validity."""

    symbol: str
    lot_size: int
    name: str = ""


class OrderRejected(Exception):  # noqa: N818 - name is part of the gateway contract
    """Raised by ``submit`` when the broker synchronously refuses an order."""

    def __init__(self, client_order_id: str, reason: str) -> None:
        super().__init__(f"{client_order_id}: {reason}")
        self.client_order_id = client_order_id
        self.reason = reason


class BrokerGateway(Protocol):
    """Order-entry and account interface."""

    def set_listeners(
        self,
        on_order: Callable[[OrderUpdate], None],
        on_fill: Callable[[Fill], None],
    ) -> None:
        """Register push callbacks (invoked on the event-loop thread)."""
        ...

    async def submit(self, intent: OrderIntent) -> str:
        """Send a limit order; return the broker order id. Raise ``OrderRejected`` on refusal.

        ``intent.client_order_id`` must be sent as the Futu ``remark`` so it can be reconciled.
        """
        ...

    async def cancel(self, broker_order_id: str) -> None:
        """Request cancellation; the outcome arrives via ``on_order``."""
        ...

    async def cancel_all(self) -> None:
        """Cancel every working order on the account (kill switch / shutdown)."""
        ...

    async def query_orders(self) -> list[OrderUpdate]:
        """Today's orders (reconciliation, never on the hot path)."""
        ...

    async def query_fills(self) -> list[Fill]:
        """Today's deals (reconciliation)."""
        ...

    async def query_positions(self) -> list[PositionSnapshot]:
        """Current positions (reconciliation)."""
        ...

    async def query_account(self) -> AccountSnapshot:
        """Account funds (reconciliation, sizing baseline)."""
        ...

    async def instrument_info(self, symbols: list[str]) -> dict[str, InstrumentInfo]:
        """Lot sizes etc. for ``symbols``; called at startup, cached by the caller."""
        ...
