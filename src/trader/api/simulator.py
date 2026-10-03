"""Paper-mode stand-in for OpenD: synthetic quotes, acks, orders and account."""

from __future__ import annotations

import random

from trader.api.models import (
    OrderResponse,
    OrderStatusResponse,
    PortfolioResponse,
    StockInfoResponse,
)
from trader.misc.types.futu import TradeSide

STARTING_CASH = 1_000_000.0
BASE_PRICE = 100.0
MEAN_REVERSION = 0.1
VOLATILITY = 0.5
PE_RATIO = 15.0
PB_RATIO = 1.5
LOT_SIZE = 100
LISTING_DATE = "2000-01-01"


class PaperSimulator:
    """Mean-reverting random-walk prices and canned broker responses."""

    def __init__(self) -> None:
        self._prices: dict[str, float] = {}

    def advance_price(self, symbol: str) -> float:
        """Step a symbol's price one tick of the random walk."""
        price = self._prices.setdefault(symbol, BASE_PRICE)
        price += MEAN_REVERSION * (BASE_PRICE - price) + VOLATILITY * random.gauss(0, 1)
        self._prices[symbol] = price
        return price

    def stock_info(self, symbol: str) -> StockInfoResponse:
        """Snapshot at the next simulated price."""
        return StockInfoResponse(
            symbol=symbol,
            name=symbol,
            price=self.advance_price(symbol),
            pe_ratio=PE_RATIO,
            pb_ratio=PB_RATIO,
            lot_size=LOT_SIZE,
            listing_date=LISTING_DATE,
        )

    @staticmethod
    def order_ack(
        symbol: str, side: TradeSide, qty: int, price: float | None, remark: str | None
    ) -> OrderResponse:
        """Acknowledge an order as SUBMITTED."""
        return OrderResponse(
            order_id=remark or f"{symbol}-{side}-{qty}",
            status="SUBMITTED",
            symbol=symbol,
            order_side=side,
            qty=qty,
            price=price,
        )

    def orders(self, symbol: str | None) -> list[OrderStatusResponse]:
        """Order history: one filled BUY for a named symbol, nothing otherwise."""
        if symbol is None:
            return []
        price = self._prices.get(symbol, BASE_PRICE)
        return [
            OrderStatusResponse(
                order_id=f"{symbol}-BUY-1",
                status="FILLED",
                symbol=symbol,
                order_side="BUY",
                qty=1,
                dealt_qty=1,
                price=price,
                avg_fill_price=price,
            )
        ]

    @staticmethod
    def portfolio(account_id: int) -> PortfolioResponse:
        """All-cash account."""
        return PortfolioResponse(
            account_id=account_id,
            total_assets=STARTING_CASH,
            market_value=0.0,
            cash=STARTING_CASH,
            available_cash=STARTING_CASH,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
        )
