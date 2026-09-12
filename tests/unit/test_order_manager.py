from __future__ import annotations

import pytest

from trader.api.client import FutuClient, OrderResponse
from trader.execution.order_manager import OrderManager, OrderState
from futu import TrdSide
from helpers import make_order


@pytest.mark.asyncio
async def test_order_manager_state_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _stub_place_order(
        self: FutuClient, symbol: str, qty: int, side: TrdSide
    ) -> OrderResponse:
        return make_order(symbol, side, qty)

    monkeypatch.setattr(FutuClient, "place_order", _stub_place_order)
    manager = OrderManager(FutuClient())
    order = await manager.place_order("700.HK", 1, TrdSide.BUY, dedupe_key="k1")
    updated = manager.transition(order, OrderState.FILLED)
    assert updated.state is OrderState.FILLED
