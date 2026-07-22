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
            return RET_OK, DataFrame(
                {
                    "code": ["700.HK"],
                    "name": ["Tencent"],
                    "last_price": [100.5],
                    "pe_ratio": [12.3],
                    "pb_ratio": [1.8],
                    "lot_size": [100],
                    "list_time": ["2004-06-16"],
                }
            )

        def close(self) -> None:
            self.closed = True

    class FakeTradeContext:
        def __init__(self, **_: object) -> None:
            self.closed = False

        def place_order(self, *_: object, **__: object) -> tuple[int, DataFrame]:
            return RET_OK, DataFrame({"order_id": ["123"], "order_status": ["SUBMITTED"]})

        def order_list_query(self, **_: object) -> tuple[int, DataFrame]:
            return RET_OK, DataFrame(
                {
                    "order_id": ["123"],
                    "code": ["700.HK"],
                    "order_status": ["FILLED"],
                    "trd_side": ["BUY"],
                    "qty": [10],
                    "dealt_qty": [10],
                    "price": [100.0],
                    "dealt_avg_price": [99.5],
                }
            )

        def position_list_query(self, **_: object) -> tuple[int, DataFrame]:
            return RET_OK, DataFrame(
                {
                    "code": ["700.HK"],
                    "qty": [10],
                    "can_sell_qty": [8],
                    "cost_price": [95.0],
                    "market_val": [1000.0],
                    "nominal_price": [100.0],
                    "pl_val": [50.0],
                }
            )

        def accinfo_query(self, **_: object) -> tuple[int, DataFrame]:
            return RET_OK, DataFrame(
                {
                    "total_assets": [1500.0],
                    "market_val": [1000.0],
                    "cash": [500.0],
                    "avl_withdrawal_cash": [450.0],
                    "unrealized_pl": [50.0],
                    "realized_pl": [25.0],
                }
            )

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(client_module, "OpenQuoteContext", FakeQuoteContext)
    monkeypatch.setattr(client_module, "OpenSecTradeContext", FakeTradeContext)

    async with FutuClient(allow_paper_fallback=False, max_retries=1) as client:
        stock = await client.get_stock_info("700.HK")
        quote = await client.get_quote("700.HK")
        order = await client.place_order("700.HK", 1, TrdSide.BUY, price=100.0, order_type="LIMIT")
        order_status = await client.get_order_status("123")
        positions = await client.get_positions()
        portfolio = await client.get_portfolio()
        condition = await client.get_portfolio_condition()
        orders = await client.list_orders("700.HK")
    assert stock.name == "Tencent"
    assert stock.pe_ratio == 12.3
    assert stock.pb_ratio == 1.8
    assert quote.symbol == "700.HK"
    assert quote.price == 100.5
    assert order.status == "SUBMITTED"
    assert order.price == 100.0
    assert order_status.status == "FILLED"
    assert positions[0].quantity == 10
    assert portfolio.total_assets == 1500.0
    assert condition.positions[0].symbol == "700.HK"
    assert orders[0].avg_fill_price == 99.5
