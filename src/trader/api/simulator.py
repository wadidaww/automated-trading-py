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

    def __init__(
        self,
        base_price: float = BASE_PRICE,
        volatility: float = VOLATILITY,
        starting_cash: float = STARTING_CASH,
        lot_size: int | None = LOT_SIZE,
    ) -> None:
        self._base_price = base_price
        self._volatility = volatility
        self._starting_cash = starting_cash
        self._lot_size = lot_size
        self._prices: dict[str, float] = {}

    def advance_price(self, symbol: str) -> float:
        """Step a symbol's price one tick of the random walk."""
        price = self._prices.setdefault(symbol, self._base_price)
        price += MEAN_REVERSION * (self._base_price - price) + self._volatility * random.gauss(0, 1)
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
            lot_size=self._lot_size,
            listing_date=LISTING_DATE,
        )

    @staticmethod
    def order_ack(
        symbol: str, side: TradeSide, qty: float, price: float | None, remark: str | None
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
        price = self._prices.get(symbol, self._base_price)
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

    def portfolio(self, account_id: int) -> PortfolioResponse:
        """All-cash account."""
        return PortfolioResponse(
            account_id=account_id,
            total_assets=self._starting_cash,
            market_value=0.0,
            cash=self._starting_cash,
            available_cash=self._starting_cash,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
        )
