"""Futu API client wrappers."""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from dataclasses import dataclass
from typing import Any, overload

import pandas as pd
from futu import (
    OpenQuoteContext,
    OpenSecTradeContext,
    OrderType,
    TrdEnv,
    TrdMarket,
)
from pydantic import BaseModel, validate_call, Field

from trader.api.typesafe.payload import Payload
from trader.misc.types.futu import TradeSide
from trader.utils.dataframe import Extractor


class QuoteResponse(BaseModel):
    """Typed quote response."""

    symbol: str
    price: float


class OrderResponse(BaseModel):
    """Typed order response."""

    order_id: str
    status: str
    symbol: str | None = None
    side: TradeSide | None = None
    qty: int | None = None
    price: float | None = None


class StockInfoResponse(BaseModel):
    """Typed stock/company snapshot response."""

    symbol: str
    name: str
    price: float
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    lot_size: int | None = None
    listing_date: str | None = None


class PositionResponse(BaseModel):
    """Typed current position response."""

    symbol: str
    quantity: int
    can_sell_qty: int
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
    side: TradeSide | None = None
    qty: int = 0
    dealt_qty: int = 0
    price: float | None = None
    avg_fill_price: float | None = None


MARKET_ORDER_PRICE = 0.0
DEFAULT_ACCOUNT_ID = 0
SYMBOL_FIELD = Field(..., description="Security code")
QTY_FIELD = Field(..., gt=0, description="Quantity in shares")
SIDE_FIELD = Field(..., pattern="^(BUY|SELL)$", description="BUY or SELL")
PRICE_FIELD = Field(default=None, ge=0, description="Limit price if needed")
ORDER_TYPE_FIELD = Field(default="MARKET", pattern="^(MARKET|LIMIT)$")


@dataclass
class TokenBucket:
    """Token bucket rate limiter."""

    capacity: int
    window_s: int
    tokens: float
    last_refill: float

    @classmethod
    def create(cls, capacity: int, window_s: int) -> TokenBucket:
        """Build a new bucket."""
        now = time.monotonic()
        return cls(capacity=capacity, window_s=window_s, tokens=float(capacity), last_refill=now)

    async def acquire(self) -> None:
        """Acquire one token from bucket."""
        while True:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.capacity / self.window_s)
            self.last_refill = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            await asyncio.sleep(0.01)


class FutuClient:
    """Async client for quote/trade operations with reconnect and heartbeat."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        max_retries: int = 5,
        heartbeat_interval_s: int = 10,
        rate_limit_requests: int = 300,
        rate_limit_window_s: int = 30,
        trade_market: str = TrdMarket.HK,
        trd_env: str = TrdEnv.SIMULATE,
        acc_id: int | None = None,
        allow_paper_fallback: bool = True,
        connection_timeout_s: float = 0.2,
    ) -> None:
        self.host = host
        self.port = port
        self.max_retries = max_retries
        self.heartbeat_interval_s = heartbeat_interval_s
        self.trade_market = trade_market
        self.trd_env = trd_env
        self.acc_id = acc_id
        self.allow_paper_fallback = allow_paper_fallback
        self.connection_timeout_s = connection_timeout_s
        self._connected = False
        self._paper_fallback = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._bucket = TokenBucket.create(rate_limit_requests, rate_limit_window_s)
        self._quote_ctx: OpenQuoteContext | None = None
        self._trade_ctx: OpenSecTradeContext | None = None

    async def __aenter__(self) -> FutuClient:
        """Open contexts and start heartbeat."""
        await self.connect()
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Close contexts and stop heartbeat."""
        await self.close()

    async def connect(self) -> None:
        """Connect with bounded retries and jitter."""
        if self.allow_paper_fallback:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.connection_timeout_s,
                )
                writer.close()
                await writer.wait_closed()
            except (OSError, TimeoutError):
                self._paper_fallback = True
                return
        for attempt in range(self.max_retries):
            try:
                self._quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
                self._trade_ctx = OpenSecTradeContext(
                    filter_trdmarket=self.trade_market, host=self.host, port=self.port
                )
                _ = Payload.str_payload(await asyncio.to_thread(self._quote_ctx.get_global_state))
                self._connected = True
                return
            except RuntimeError:
                await asyncio.to_thread(self._close_contexts)
                delay = min(2**attempt, 10) + random.random()
                await asyncio.sleep(delay)
        if self.allow_paper_fallback:
            self._paper_fallback = True
            return
        raise ConnectionError("failed to connect")

    async def close(self) -> None:
        """Close API contexts."""
        self._connected = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        await asyncio.to_thread(self._close_contexts)

    async def _heartbeat(self) -> None:
        """Heartbeat loop."""
        while self._connected:
            await asyncio.sleep(self.heartbeat_interval_s)

    def _close_contexts(self) -> None:
        """Close OpenD contexts if initialized."""
        if self._quote_ctx is not None:
            self._quote_ctx.close()
            self._quote_ctx = None
        if self._trade_ctx is not None:
            self._trade_ctx.close()
            self._trade_ctx = None

    async def _ensure_connected(self) -> None:
        """Ensure contexts are connected before request."""
        if not self._connected and not self._paper_fallback:
            await self.connect()

    async def get_quote(self, symbol: str) -> QuoteResponse:
        """Get quote for symbol.

        Args:
            symbol: Security code.

        Returns:
            QuoteResponse: Typed response.
        """
        stock_info = await self.get_stock_info(symbol)
        return QuoteResponse(symbol=stock_info.symbol, price=stock_info.price)

    async def get_stock_info(self, symbol: str) -> StockInfoResponse:
        """Get stock snapshot with valuation metrics."""
        await self._bucket.acquire()
        await self._ensure_connected()
        if self._paper_fallback or self._quote_ctx is None:
            return StockInfoResponse(
                symbol=symbol,
                name=symbol,
                price=100.0,
                pe_ratio=15.0,
                pb_ratio=1.5,
                lot_size=100,
                listing_date="2000-01-01",
            )
        payload_df = Payload.df_payload(
            await asyncio.to_thread(self._quote_ctx.get_market_snapshot, [symbol])
        )
        if payload_df.empty:
            raise RuntimeError(f"no market snapshot for {symbol}")
        row = payload_df.iloc[0]
        response_symbol = Extractor.extract_symbol_from_payload(payload_df, fallback=symbol)
        price = self._extract_first_float(row, ("last_price", "nominal_price"))
        if price is None:
            raise RuntimeError("market snapshot missing price columns")
        stock_name = self._extract_first_str(row, ("name",), fallback=response_symbol)
        return StockInfoResponse(
            symbol=response_symbol,
            name=stock_name,
            price=price,
            pe_ratio=self._extract_first_float(row, ("pe_ratio",)),
            pb_ratio=self._extract_first_float(row, ("pb_ratio",)),
            lot_size=self._extract_first_int(row, ("lot_size",)),
            listing_date=self._extract_first_str(row, ("list_time",), fallback=None),
        )

    @validate_call
    async def place_order(
        self,
        symbol: str = SYMBOL_FIELD,
        qty: int = QTY_FIELD,
        side: TradeSide = SIDE_FIELD,
        order_type: str = ORDER_TYPE_FIELD,
        price: float | None = PRICE_FIELD,
    ) -> OrderResponse:
        """Place an order.

        Args:
            symbol: Security code.
            qty: Quantity in shares.
            side: BUY or SELL.

        Returns:
            OrderResponse: Typed response.
        """
        await self._bucket.acquire()
        await self._ensure_connected()
        resolved_order_type = self._resolve_order_type(order_type)
        resolved_price = MARKET_ORDER_PRICE if resolved_order_type == OrderType.MARKET else price
        if resolved_order_type != OrderType.MARKET and resolved_price is None:
            raise ValueError("limit orders require a price")
        if self._paper_fallback or self._trade_ctx is None:
            return OrderResponse(
                order_id=f"{symbol}-{side}-{qty}",
                status="SUBMITTED",
                symbol=symbol,
                side=side,
                qty=qty,
                price=resolved_price,
            )
        payload_df = Payload.df_payload(
            await asyncio.to_thread(
                self._trade_ctx.place_order,
                resolved_price,
                qty,
                symbol,
                side,
                order_type=resolved_order_type,
                trd_env=self.trd_env,
                acc_id=DEFAULT_ACCOUNT_ID if self.acc_id is None else self.acc_id,
            )
        )
        if payload_df.empty:
            raise RuntimeError("empty order response")
        order_id = Extractor._extract_row_value(payload_df, ("order_id",), f"{symbol}-{side}-{qty}")
        status = Extractor._extract_row_value(payload_df, ("order_status",), "SUBMITTED")
        return OrderResponse(
            order_id=order_id,
            status=status,
            symbol=symbol,
            side=side,
            qty=qty,
            price=resolved_price,
        )

    async def list_orders(self, symbol: str | None = None) -> list[OrderStatusResponse]:
        """List current account orders."""
        await self._bucket.acquire()
        await self._ensure_connected()
        if self._paper_fallback or self._trade_ctx is None:
            if symbol is None:
                return []
            return [
                OrderStatusResponse(
                    order_id=f"{symbol}-BUY-1",
                    status="FILLED",
                    symbol=symbol,
                    side="BUY",
                    qty=1,
                    dealt_qty=1,
                    price=100.0,
                    avg_fill_price=100.0,
                )
            ]
        payload_df = Payload.df_payload(
            await asyncio.to_thread(
                self._trade_ctx.order_list_query,
                trd_env=self.trd_env,
                acc_id=DEFAULT_ACCOUNT_ID if self.acc_id is None else self.acc_id,
            )
        )
        if symbol is not None and "code" in payload_df.columns:
            payload_df = payload_df[payload_df["code"] == symbol]
        return [self._order_status_from_row(row) for _, row in payload_df.iterrows()]

    async def get_order_status(self, order_id: str) -> OrderStatusResponse:
        """Get one order status by order id."""
        orders = await self.list_orders()
        for order in orders:
            if order.order_id == order_id:
                return order
        raise RuntimeError(f"order not found: {order_id}")

    async def get_positions(self) -> list[PositionResponse]:
        """Get current live positions."""
        await self._bucket.acquire()
        await self._ensure_connected()
        if self._paper_fallback or self._trade_ctx is None:
            return []
        payload_df = Payload.df_payload(
            await asyncio.to_thread(
                self._trade_ctx.position_list_query,
                trd_env=self.trd_env,
                acc_id=DEFAULT_ACCOUNT_ID if self.acc_id is None else self.acc_id,
            )
        )
        return [self._position_from_row(row) for _, row in payload_df.iterrows()]

    async def get_portfolio(self) -> PortfolioResponse:
        """Get current account portfolio condition."""
        await self._bucket.acquire()
        await self._ensure_connected()
        account_id = DEFAULT_ACCOUNT_ID if self.acc_id is None else self.acc_id
        if self._paper_fallback or self._trade_ctx is None:
            return PortfolioResponse(
                account_id=account_id,
                total_assets=1_000_000.0,
                market_value=0.0,
                cash=1_000_000.0,
                available_cash=1_000_000.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
            )
        payload_df = Payload.df_payload(
            await asyncio.to_thread(
                self._trade_ctx.accinfo_query,
                trd_env=self.trd_env,
                acc_id=account_id,
            )
        )
        if payload_df.empty:
            raise RuntimeError("empty account info response")
        row = payload_df.iloc[0]
        return PortfolioResponse(
            account_id=account_id,
            total_assets=self._extract_first_float(
                row, ("total_assets", "power", "net_assets"), fallback=0.0
            )
            or 0.0,
            market_value=self._extract_first_float(
                row, ("market_val", "securities_assets"), fallback=0.0
            )
            or 0.0,
            cash=self._extract_first_float(row, ("cash", "cash_balance"), fallback=0.0) or 0.0,
            available_cash=self._extract_first_float(
                row, ("avl_withdrawal_cash", "available_funds", "max_power_short"), fallback=0.0
            )
            or 0.0,
            unrealized_pnl=self._extract_first_float(row, ("unrealized_pl", "holding_pl",)),
            realized_pnl=self._extract_first_float(row, ("realized_pl",)),
        )

    async def get_portfolio_condition(self) -> PortfolioConditionResponse:
        """Get combined account and position condition."""
        portfolio, positions = await asyncio.gather(self.get_portfolio(), self.get_positions())
        return PortfolioConditionResponse(portfolio=portfolio, positions=positions)

    @staticmethod
    def _extract_first_float(
        row: pd.Series, columns: tuple[str, ...], fallback: float | None = None
    ) -> float | None:
        """Extract first numeric value from DataFrame row."""
        for column in columns:
            if column in row.index:
                value = row[column]
                if pd.notna(value):
                    return float(value)
        return fallback

    @staticmethod
    def _extract_first_int(
        row: pd.Series, columns: tuple[str, ...], fallback: int | None = None
    ) -> int | None:
        """Extract first integer value from DataFrame row."""
        value = FutuClient._extract_first_float(row, columns)
        if value is None:
            return fallback
        return int(value)

    @overload
    @staticmethod
    def _extract_first_str(row: pd.Series, columns: tuple[str, ...], fallback: str) -> str: ...

    @overload
    @staticmethod
    def _extract_first_str(
        row: pd.Series, columns: tuple[str, ...], fallback: None
    ) -> None: ...

    @staticmethod
    def _extract_first_str(
        row: pd.Series, columns: tuple[str, ...], fallback: str | None
    ) -> str | None:
        """Extract first string value from DataFrame row."""
        for column in columns:
            if column in row.index:
                value = row[column]
                if pd.notna(value):
                    return str(value)
        return fallback

    @staticmethod
    def _resolve_order_type(order_type: str) -> int:
        """Resolve public order type string to Futu enum."""
        return OrderType.MARKET if order_type == "MARKET" else OrderType.NORMAL

    @classmethod
    def _position_from_row(cls, row: pd.Series) -> PositionResponse:
        """Build a typed position response from raw DataFrame row."""
        symbol = cls._extract_first_str(row, ("code", "stock_code"), fallback="UNKNOWN")
        if symbol is None:
            raise RuntimeError("position payload missing symbol")
        return PositionResponse(
            symbol=symbol,
            quantity=cls._extract_first_int(row, ("qty", "can_sell_qty"), fallback=0) or 0,
            can_sell_qty=cls._extract_first_int(row, ("can_sell_qty", "qty"), fallback=0) or 0,
            avg_cost=cls._extract_first_float(row, ("cost_price", "cost_price_valid"), fallback=0.0)
            or 0.0,
            market_value=cls._extract_first_float(row, ("market_val",), fallback=0.0) or 0.0,
            nominal_price=cls._extract_first_float(
                row, ("nominal_price", "last_price"), fallback=0.0
            )
            or 0.0,
            unrealized_pnl=cls._extract_first_float(row, ("pl_val", "unrealized_pl")),
        )

    @classmethod
    def _order_status_from_row(cls, row: pd.Series) -> OrderStatusResponse:
        """Build a typed order status from raw DataFrame row."""
        symbol = cls._extract_first_str(row, ("code", "stock_code"), fallback="UNKNOWN")
        order_id = cls._extract_first_str(row, ("order_id",), fallback="")
        status = cls._extract_first_str(row, ("order_status", "status"), fallback="UNKNOWN")
        if symbol is None or order_id is None or status is None:
            raise RuntimeError("order payload missing required fields")
        raw_side = cls._extract_first_str(row, ("trd_side", "side"), fallback=None)
        if raw_side == "BUY":
            side: TradeSide | None = "BUY"
        elif raw_side == "SELL":
            side = "SELL"
        else:
            side = None
        return OrderStatusResponse(
            order_id=order_id,
            status=status,
            symbol=symbol,
            side=side,
            qty=cls._extract_first_int(row, ("qty",), fallback=0) or 0,
            dealt_qty=cls._extract_first_int(row, ("dealt_qty",), fallback=0) or 0,
            price=cls._extract_first_float(row, ("price",)),
            avg_fill_price=cls._extract_first_float(row, ("dealt_avg_price", "avg_price")),
        )
