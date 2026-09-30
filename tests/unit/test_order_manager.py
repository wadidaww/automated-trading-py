from __future__ import annotations

from typing import Any

import pytest

from trader.api.client import OrderResponse
from trader.core.orders import ManagedOrder, OrderIntent, OrderStatus
from trader.execution.order_manager import DuplicateOrderError, OrderManager, TradingHaltedError
from trader.risk.kill_switch import KillSwitch


class RecordingClient:
    """Records place_order calls and returns a configurable ack."""

    def __init__(self, status: str = "SUBMITTED", fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = status
        self.fail = fail

    async def place_order(self, symbol: str, qty: int, side: str, **kwargs: Any) -> OrderResponse:
        self.calls.append({"symbol": symbol, "qty": qty, "side": side, **kwargs})
        if self.fail:
            raise RuntimeError("timeout")
        return OrderResponse(order_id="987654", status=self.status, symbol=symbol)


def _intent(client_order_id: str = "c0123456789abcdef012") -> OrderIntent:
    return OrderIntent(
        client_order_id=client_order_id,
        symbol="HK.00700",
        side="BUY",
        qty=100,
        limit_price=300.0,
        strategy="test",
        created_ns=1,
        reference_price=300.1,
    )


async def test_place_sends_remark_and_stores_broker_id() -> None:
    client = RecordingClient()
    manager = OrderManager(client)  # type: ignore[arg-type]
    order = await manager.place(_intent())
    assert client.calls[0]["remark"] == "c0123456789abcdef012"
    assert client.calls[0]["order_type"] == "LIMIT"
    assert order.broker_order_id == "987654"
    assert order.status is OrderStatus.WORKING
    assert manager.working_orders() == [order]


async def test_duplicate_intent_is_never_resent() -> None:
    client = RecordingClient()
    manager = OrderManager(client)  # type: ignore[arg-type]
    await manager.place(_intent())
    with pytest.raises(DuplicateOrderError):
        await manager.place(_intent())
    assert len(client.calls) == 1


async def test_failed_send_is_unknown_and_still_working() -> None:
    manager = OrderManager(RecordingClient(fail=True))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        await manager.place(_intent())
    order = manager.orders["c0123456789abcdef012"]
    assert order.status is OrderStatus.UNKNOWN
    assert order.status.is_working


@pytest.mark.parametrize(
    ("futu_status", "expected"),
    [
        ("FILLED_ALL", OrderStatus.FILLED),
        ("SUBMIT_FAILED", OrderStatus.REJECTED),
        ("WAITING_SUBMIT", OrderStatus.PENDING_NEW),
    ],
)
async def test_ack_status_uses_full_futu_map(futu_status: str, expected: OrderStatus) -> None:
    manager = OrderManager(RecordingClient(status=futu_status))  # type: ignore[arg-type]
    order = await manager.place(_intent())
    assert order.status is expected


def test_illegal_transition_is_refused() -> None:
    manager = OrderManager(RecordingClient())  # type: ignore[arg-type]
    order = ManagedOrder(intent=_intent("x"))
    assert manager.apply_status(order, OrderStatus.FILLED)
    assert not manager.apply_status(order, OrderStatus.WORKING)
    assert order.status is OrderStatus.FILLED


async def test_nothing_is_sent_once_the_kill_switch_trips() -> None:
    client = RecordingClient()
    kill_switch = KillSwitch()
    manager = OrderManager(client, kill_switch=kill_switch)  # type: ignore[arg-type]
    kill_switch.trip("manual")
    with pytest.raises(TradingHaltedError):
        await manager.place(_intent())
    assert client.calls == []
    assert manager.orders == {}
