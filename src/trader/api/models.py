"""Typed broker response models."""

from __future__ import annotations

from pydantic import BaseModel

from trader.misc.types.futu import TradeSide


class QuoteResponse(BaseModel):
    """Typed quote response."""

    symbol: str
    price: float


class OrderResponse(BaseModel):
    """Typed order response."""

    order_id: str
    status: str
    symbol: str | None = None
    order_side: TradeSide | None = None
    qty: float | None = None
    price: float | None = None


class StockInfoResponse(BaseModel):
    """Typed stock/company snapshot response."""

    symbol: str
    name: str
    price: float
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    lot_size: int | None = None
    # Crypto instrument metadata (equities use lot_size). tick_size comes from the broker snapshot;
    # the quantity step and minimum come from the client's configured instrument rules.
    tick_size: float | None = None
    qty_step: float | None = None
    min_qty: float | None = None
    listing_date: str | None = None


class PositionResponse(BaseModel):
    """Typed current position response."""

    symbol: str
    quantity: float
    can_sell_qty: float
    avg_cost: float
    market_value: float
    nominal_price: float
    unrealized_pnl: float | None = None


class PortfolioResponse(BaseModel):
    """Typed portfolio/account response."""

    account_id: int
    total_assets: float
    market_value: float
    cash: float
    available_cash: float
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None


class PortfolioConditionResponse(BaseModel):
    """Current account and holding condition."""

    portfolio: PortfolioResponse
    positions: list[PositionResponse]


class OrderStatusResponse(BaseModel):
    """Typed order status response."""

    order_id: str
    status: str
    symbol: str
    order_side: TradeSide | None = None
    qty: float = 0.0
    dealt_qty: float = 0.0
    price: float | None = None
    avg_fill_price: float | None = None


def find_position_by_symbol(
    positions: list[PositionResponse], symbol: str
) -> PositionResponse | None:
    """Find a position for a specific symbol, or None when not held."""
    return next((pos for pos in positions if pos.symbol == symbol), None)
