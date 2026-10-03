"""Map raw OpenD DataFrame payloads onto typed responses."""

from __future__ import annotations

from typing import Any

import pandas as pd

from trader.api.models import (
    OrderStatusResponse,
    PortfolioResponse,
    PositionResponse,
    StockInfoResponse,
)
from trader.misc.types.futu import TradeSide
from trader.utils.dataframe import first_value

TRADE_SIDES: dict[str, TradeSide] = {"BUY": "BUY", "SELL": "SELL"}
SYMBOL_COLUMNS = ("code", "stock_code")
PORTFOLIO_AMOUNT_FIELDS: dict[str, tuple[str, ...]] = {
    "total_assets": ("total_assets", "power", "net_assets"),
    "market_value": ("market_val", "securities_assets"),
    "cash": ("cash", "cash_balance"),
    "available_cash": ("avl_withdrawal_cash", "available_funds", "max_power_short"),
}


def _as_int(value: Any) -> int:
    """Coerce a raw cell to int through float so fractional strings truncate."""
    return int(float(value))


def stock_info_from_snapshot(payload_df: pd.DataFrame, symbol: str) -> StockInfoResponse:
    """Build a typed stock snapshot from a market snapshot payload.

    Raises:
        RuntimeError: When the payload is empty or lacks a price column.
    """
    if payload_df.empty:
        raise RuntimeError(f"no market snapshot for {symbol}")
    row = payload_df.iloc[0]
    response_symbol = first_value(row, SYMBOL_COLUMNS, str, symbol)
    price = first_value(row, ("last_price", "nominal_price"), float)
    if price is None:
        raise RuntimeError("market snapshot missing price columns")
    return StockInfoResponse(
        symbol=response_symbol,
        name=first_value(row, ("name",), str, response_symbol),
        price=price,
        pe_ratio=first_value(row, ("pe_ratio",), float),
        pb_ratio=first_value(row, ("pb_ratio",), float),
        lot_size=first_value(row, ("lot_size",), _as_int),
        listing_date=first_value(row, ("list_time",), str),
    )


def portfolio_from_row(row: pd.Series, account_id: int) -> PortfolioResponse:
    """Build a typed portfolio from an account info payload row."""
    amounts: dict[str, Any] = {
        field: first_value(row, columns, float, 0.0)
        for field, columns in PORTFOLIO_AMOUNT_FIELDS.items()
    }
    return PortfolioResponse(
        account_id=account_id,
        unrealized_pnl=first_value(row, ("unrealized_pl", "holding_pl"), float),
        realized_pnl=first_value(row, ("realized_pl",), float),
        **amounts,
    )


def position_from_row(row: pd.Series) -> PositionResponse:
    """Build a typed position from a position payload row."""
    return PositionResponse(
        symbol=first_value(row, SYMBOL_COLUMNS, str, "UNKNOWN"),
        quantity=first_value(row, ("qty",), _as_int, 0),
        can_sell_qty=first_value(row, ("can_sell_qty",), _as_int, 0),
        avg_cost=first_value(row, ("cost_price", "cost_price_valid"), float, 0.0),
        market_value=first_value(row, ("market_val",), float, 0.0),
        nominal_price=first_value(row, ("nominal_price", "last_price"), float, 0.0),
        unrealized_pnl=first_value(row, ("pl_val", "unrealized_pl"), float),
    )


def order_status_from_row(row: pd.Series) -> OrderStatusResponse:
    """Build a typed order status from an order payload row.

    Raises:
        RuntimeError: When the row carries no order id.
    """
    order_id = first_value(row, ("order_id",), str, "")
    if not order_id:
        raise RuntimeError("order payload missing required field: order_id")
    raw_side = first_value(row, ("trd_side", "side"), str, "")
    return OrderStatusResponse(
        order_id=order_id,
        status=first_value(row, ("order_status", "status"), str, "UNKNOWN"),
        symbol=first_value(row, SYMBOL_COLUMNS, str, "UNKNOWN"),
        order_side=TRADE_SIDES.get(raw_side),
        qty=first_value(row, ("qty",), _as_int, 0),
        dealt_qty=first_value(row, ("dealt_qty",), _as_int, 0),
        price=first_value(row, ("price",), float),
        avg_fill_price=first_value(row, ("dealt_avg_price", "avg_price"), float),
    )
