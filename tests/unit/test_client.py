from __future__ import annotations

import pytest
from futu import TrdSide

from trader.api import client as client_module
from trader.api.client import FutuClient
from helpers import FakeQuoteContext, FakeTradeContext


@pytest.mark.asyncio
async def test_client_get_quote_and_place_order(monkeypatch: pytest.MonkeyPatch) -> None:
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
