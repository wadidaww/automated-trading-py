from __future__ import annotations

import pytest

from trader.api.client import (
    FutuClient,
    OrderResponse,
    PortfolioConditionResponse,
    StockInfoResponse,
)
from trader.execution.trading_service import TradingService
from helpers import make_order, make_portfolio_condition, make_position, make_stock_info


@pytest.mark.asyncio
async def test_trading_service_places_buy_and_sell_orders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prices = iter([95.0, 95.0, 110.0])

    async def _stock_info(self: FutuClient, symbol: str) -> StockInfoResponse:
        return make_stock_info(symbol, next(prices) if symbol == "700.HK" else 0.0)

    async def _portfolio(self: FutuClient) -> PortfolioConditionResponse:
        return make_portfolio_condition(
            positions=[make_position("700.HK", 5, nominal_price=95.0, market_value=475.0)]
        )

    async def _place_order(
        self: FutuClient,
        symbol: str,
        qty: int,
        side: str,
        price: float | None = None,
        order_type: str = "MARKET",
    ) -> OrderResponse:
        return make_order(symbol, side, qty, price=price)

    monkeypatch.setattr(FutuClient, "get_stock_info", _stock_info)
    monkeypatch.setattr(FutuClient, "get_portfolio_condition", _portfolio)
    monkeypatch.setattr(FutuClient, "place_order", _place_order)

    service = TradingService(FutuClient())
    evaluation = await service.evaluate_targets("700.HK", buy_target=95.0, sell_target=105.0)
    buy_order = await service.place_buy_order("700.HK", qty=2, buy_target=95.0)
    sell_order = await service.place_sell_order("700.HK", qty=2, sell_target=105.0)

    assert evaluation.buy_target_hit is True
    assert evaluation.sell_target_hit is False
    assert evaluation.held_quantity == 5
    assert buy_order.order_side == "BUY"
    assert buy_order.price == 95.0
    assert sell_order.order_side == "SELL"
    assert sell_order.price == 105.0


@pytest.mark.asyncio
async def test_trading_service_rejects_orders_when_targets_not_met(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _stock_info(self: FutuClient, symbol: str) -> StockInfoResponse:
        return make_stock_info(symbol, 110.0)

    async def _portfolio(self: FutuClient) -> PortfolioConditionResponse:
        return make_portfolio_condition(total_assets=1000.0, market_value=0.0, cash=1000.0)

    monkeypatch.setattr(FutuClient, "get_stock_info", _stock_info)
    monkeypatch.setattr(FutuClient, "get_portfolio_condition", _portfolio)

    service = TradingService(FutuClient())

    with pytest.raises(ValueError, match="buy target not reached"):
        await service.place_buy_order("700.HK", qty=1, buy_target=100.0)

    with pytest.raises(ValueError, match="insufficient position to sell"):
        await service.place_sell_order("700.HK", qty=1, sell_target=120.0)
