"""Order domain model: sides, statuses, intents and the order state machine.

The internal ``OrderStatus`` is a reduction of Futu's ``OrderStatus`` set. Every Futu status string
maps to exactly one internal status via ``from_futu_status``; unknown strings map to ``UNKNOWN``,
which risk treats as *working* (fail closed: exposure is counted until reconciliation says
otherwise).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Literal

type Side = Literal["BUY", "SELL"]


class OrderStatus(Enum):
    """Internal order lifecycle states."""

    PENDING_NEW = "PENDING_NEW"  # intent created, not yet acknowledged by the broker
    WORKING = "WORKING"  # acknowledged, resting, nothing filled
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    PENDING_CANCEL = "PENDING_CANCEL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"  # includes cancelled after a partial fill
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_terminal(self) -> bool:
        """Whether no further transitions are possible."""
        return self in _TERMINAL

    @property
    def is_working(self) -> bool:
        """Whether the order may still fill (counts towards exposure and open-order limits)."""
        return not self.is_terminal


_TERMINAL: Final[frozenset[OrderStatus]] = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}
)

FUTU_STATUS_MAP: Final[dict[str, OrderStatus]] = {
    "UNSUBMITTED": OrderStatus.PENDING_NEW,
    "WAITING_SUBMIT": OrderStatus.PENDING_NEW,
    "SUBMITTING": OrderStatus.PENDING_NEW,
    "SUBMITTED": OrderStatus.WORKING,
    "FILLED_PART": OrderStatus.PARTIALLY_FILLED,
    "FILLED_ALL": OrderStatus.FILLED,
    "CANCELLING_PART": OrderStatus.PENDING_CANCEL,
    "CANCELLING_ALL": OrderStatus.PENDING_CANCEL,
    "CANCELLED_PART": OrderStatus.CANCELLED,
    "CANCELLED_ALL": OrderStatus.CANCELLED,
    "SUBMIT_FAILED": OrderStatus.REJECTED,
    "FAILED": OrderStatus.REJECTED,
    "DISABLED": OrderStatus.REJECTED,
    "DELETED": OrderStatus.CANCELLED,
    "FILL_CANCELLED": OrderStatus.CANCELLED,
    # TIMEOUT means "we do not know" — keep counting exposure until reconciled.
    "TIMEOUT": OrderStatus.UNKNOWN,
}


def from_futu_status(raw: str) -> OrderStatus:
    """Map a Futu ``OrderStatus`` string (e.g. ``FILLED_PART``) to the internal status."""
    return FUTU_STATUS_MAP.get(raw.strip().upper(), OrderStatus.UNKNOWN)


_ALLOWED: Final[dict[OrderStatus, frozenset[OrderStatus]]] = {
    OrderStatus.PENDING_NEW: frozenset(
        {
            OrderStatus.WORKING,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.UNKNOWN,
        }
    ),
    OrderStatus.WORKING: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.UNKNOWN,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.CANCELLED,
            OrderStatus.UNKNOWN,
        }
    ),
    OrderStatus.PENDING_CANCEL: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.WORKING,  # cancel rejected
            OrderStatus.UNKNOWN,
        }
    ),
    OrderStatus.UNKNOWN: frozenset(OrderStatus) - {OrderStatus.UNKNOWN},
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    """Whether ``current → target`` is a legal lifecycle transition (same state is a no-op)."""
    return current == target or target in _ALLOWED[current]


def make_client_order_id(strategy: str, symbol: str, side: Side, seq: int) -> str:
    """Deterministic, short client order id sent to Futu in ``remark``.

    Deterministic so that a retried intent produces the same id and can be deduplicated and
    reconciled against the broker after a restart. Kept to 20 characters (Futu limits remark
    length).

    Args:
        strategy: Strategy / model name.
        symbol: Futu code.
        side: BUY or SELL.
        seq: Monotonic per-process intent sequence (persisted across restarts by the store).

    Returns:
        str: ``c`` + 19 hex characters.
    """
    digest = hashlib.blake2b(f"{strategy}|{symbol}|{side}|{seq}".encode(), digest_size=10)
    return "c" + digest.hexdigest()[:19]


@dataclass(slots=True)
class OrderIntent:
    """What the strategy + risk decided to send. Immutable once approved."""

    client_order_id: str
    symbol: str
    side: Side
    qty: int
    limit_price: float
    strategy: str
    created_ns: int  # clock.now_ns() at decision time
    reference_price: float  # mid / last used for the price-band check


@dataclass(slots=True)
class Fill:
    """One execution (Futu deal)."""

    deal_id: str
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: Side
    qty: int
    price: float
    ts_ns: int
    fee: float = 0.0


@dataclass(slots=True)
class ManagedOrder:
    """Order tracked by the OMS through its lifecycle."""

    intent: OrderIntent
    status: OrderStatus = OrderStatus.PENDING_NEW
    broker_order_id: str | None = None
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    reject_reason: str | None = None
    updated_ns: int = 0
    fills: list[Fill] = field(default_factory=list)

    @property
    def remaining_qty(self) -> int:
        """Quantity that may still fill."""
        return 0 if self.status.is_terminal else max(self.intent.qty - self.filled_qty, 0)
