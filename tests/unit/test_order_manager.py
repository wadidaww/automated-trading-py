from __future__ import annotations

import pytest

from futu_trader.api.client import FutuClient
from futu_trader.execution.order_manager import OrderManager, OrderState


@pytest.mark.asyncio
async def test_order_manager_state_transition() -> None:
    manager = OrderManager(FutuClient())
    order = await manager.place_order("700.HK", 1, "BUY", dedupe_key="k1")
    updated = manager.transition(order, OrderState.FILLED)
    assert updated.state is OrderState.FILLED
