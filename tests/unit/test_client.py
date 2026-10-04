from __future__ import annotations

import pytest
from futu import TrdSide
from futu.common.constant import ContextStatus

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


@pytest.mark.asyncio
async def test_paper_fallback_simulates_varying_prices() -> None:
    async with FutuClient(allow_paper_fallback=True, max_retries=0) as client:
        prices = []
        for _ in range(50):
            stock = await client.get_stock_info("700.HK")
            prices.append(stock.price)
    assert len(prices) == 50
    assert len(set(prices)) > 1, "paper fallback should produce varying prices"


@pytest.mark.asyncio
async def test_verify_handshake_reports_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ReadyContext:
        status = ContextStatus.READY

        def close(self) -> None:
            return None

    monkeypatch.setattr(client_module, "OpenQuoteContext", lambda *args, **kwargs: _ReadyContext())
    client = FutuClient()
    assert await client.verify_handshake(timeout_s=0.5) is True


@pytest.mark.asyncio
async def test_verify_handshake_times_out_and_closes_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PendingContext:
        status = ContextStatus.CONNECTING

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    context = _PendingContext()
    monkeypatch.setattr(client_module, "OpenQuoteContext", lambda *args, **kwargs: context)
    client = FutuClient()
    assert await client.verify_handshake(timeout_s=0.1) is False
    assert context.closed is True


@pytest.mark.asyncio
async def test_verify_handshake_reports_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken_context(*args: object, **kwargs: object) -> object:
        raise RuntimeError("cannot build context")

    monkeypatch.setattr(client_module, "OpenQuoteContext", _broken_context)
    client = FutuClient()
    assert await client.verify_handshake(timeout_s=0.1) is False


async def test_client_live_account_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "OpenQuoteContext", FakeQuoteContext)
    monkeypatch.setattr(client_module, "OpenSecTradeContext", FakeTradeContext)

    async with FutuClient(
        allow_paper_fallback=False, max_retries=1, trd_env="REAL", acc_id=123456789012345678
    ) as client:
        await client.verify_account()
        await client.unlock_trade("md5hash")
        await client.place_order(
            "HK.00700", 100, "BUY", price=300.0, order_type="LIMIT", remark="c1"
        )
        await client.cancel_all_orders()
        trade_ctx = client._trade_ctx
    assert isinstance(trade_ctx, FakeTradeContext)
    assert trade_ctx.unlocked_with == "md5hash"
    assert trade_ctx.place_kwargs["remark"] == "c1"
    assert trade_ctx.place_kwargs["acc_id"] == 123456789012345678
    assert trade_ctx.cancel_all_calls == 1


async def test_client_rejects_unlisted_real_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "OpenQuoteContext", FakeQuoteContext)
    monkeypatch.setattr(client_module, "OpenSecTradeContext", FakeTradeContext)

    async with FutuClient(
        allow_paper_fallback=False, max_retries=1, trd_env="REAL", acc_id=42
    ) as client:
        with pytest.raises(RuntimeError, match="not found"):
            await client.verify_account()


async def test_real_trading_never_defaults_the_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "OpenQuoteContext", FakeQuoteContext)
    monkeypatch.setattr(client_module, "OpenSecTradeContext", FakeTradeContext)

    async with FutuClient(allow_paper_fallback=False, max_retries=1, trd_env="REAL") as client:
        with pytest.raises(ValueError, match="acc_id"):
            await client.place_order("HK.00700", 100, "BUY", price=300.0, order_type="LIMIT")
