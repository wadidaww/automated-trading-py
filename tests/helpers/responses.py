"""Factories for typed trader API responses used across tests."""

from __future__ import annotations

from trader.api.client import (
    OrderResponse,
    PortfolioConditionResponse,
    PortfolioResponse,
    PositionResponse,
    StockInfoResponse,
)


def make_stock_info(
    symbol: str,
    price: float,
    *,
    name: str = "Tencent",
    pe_ratio: float = 12.0,
    pb_ratio: float = 1.5,
    lot_size: int = 100,
    listing_date: str = "2004-06-16",
) -> StockInfoResponse:
    """Build a stock snapshot response."""
    return StockInfoResponse(
        symbol=symbol,
        name=name,
        price=price,
        pe_ratio=pe_ratio,
        pb_ratio=pb_ratio,
        lot_size=lot_size,
        listing_date=listing_date,
    )


def make_position(
    symbol: str,
    quantity: int,
    *,
    can_sell_qty: int | None = None,
    avg_cost: float = 90.0,
    market_value: float | None = None,
    nominal_price: float | None = None,
) -> PositionResponse:
    """Build a position response with sensible defaults."""
    sellable = quantity if can_sell_qty is None else can_sell_qty
    last_price = avg_cost if nominal_price is None else nominal_price
    value = quantity * last_price if market_value is None else market_value
    return PositionResponse(
        symbol=symbol,
        quantity=quantity,
        can_sell_qty=sellable,
        avg_cost=avg_cost,
        market_value=value,
        nominal_price=last_price,
    )


def make_portfolio_condition(
    *,
    positions: list[PositionResponse] | None = None,
    account_id: int = 1,
    total_assets: float = 2000.0,
    market_value: float = 1000.0,
    cash: float = 1000.0,
    available_cash: float = 800.0,
) -> PortfolioConditionResponse:
    """Build an account-and-positions condition with defaults."""
    return PortfolioConditionResponse(
        portfolio=PortfolioResponse(
            account_id=account_id,
            total_assets=total_assets,
            market_value=market_value,
            cash=cash,
            available_cash=available_cash,
        ),
        positions=positions if positions is not None else [],
    )


def make_order(
    symbol: str,
    side: str,
    qty: int,
    *,
    order_id: str | None = None,
    status: str = "SUBMITTED",
    price: float | None = None,
) -> OrderResponse:
    """Build an order response with a deterministic id."""
    return OrderResponse(
        order_id=order_id if order_id is not None else f"{symbol}-{side}-{qty}",
        status=status,
        symbol=symbol,
        order_side=side,
        qty=qty,
        price=price,
    )
