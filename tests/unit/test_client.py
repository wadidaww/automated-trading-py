from __future__ import annotations

import pytest
from futu import (
    RET_OK,
    TrdSide,
)
from pandas import DataFrame

from trader.api import client as client_module
from trader.api.client import FutuClient


@pytest.mark.asyncio
async def test_client_get_quote_and_place_order(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeQuoteContext:
        def __init__(self, **_: object) -> None:
            self.closed = False

        def get_global_state(self) -> tuple[int, str]:
            return RET_OK, "ok"

        def get_market_snapshot(self, _: list[str]) -> tuple[int, DataFrame]:
            return RET_OK, DataFrame({"code": ["700.HK"], "last_price": [100.5]})

        def close(self) -> None:
            self.closed = True

    class FakeTradeContext:
        def __init__(self, **_: object) -> None:
            self.closed = False

        def place_order(self, *_: object, **__: object) -> tuple[int, DataFrame]:
            return RET_OK, DataFrame({"order_id": ["123"], "order_status": ["SUBMITTED"]})

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(client_module, "OpenQuoteContext", FakeQuoteContext)
    monkeypatch.setattr(client_module, "OpenSecTradeContext", FakeTradeContext)

    async with FutuClient(allow_paper_fallback=False, max_retries=1) as client:
        quote = await client.get_quote("700.HK")
        order = await client.place_order("700.HK", 1, TrdSide.BUY)
    assert quote.symbol == "700.HK"
    assert quote.price == 100.5
    assert order.status == "SUBMITTED"
