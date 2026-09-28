"""Futu API client wrappers."""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections.abc import Callable
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
from futu.common.constant import ContextStatus
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
    order_side: TradeSide | None = None
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
    order_side: TradeSide | None = None
    qty: int = 0
    dealt_qty: int = 0
    price: float | None = None
    avg_fill_price: float | None = None


MARKET_ORDER_PRICE = 0.0
DEFAULT_ACCOUNT_ID = 0
SIMULATED_STARTING_CASH = 1_000_000.0
SIMULATED_BASE_PRICE = 100.0
SIMULATED_MEAN_REVERSION = 0.1
SIMULATED_VOLATILITY = 0.5
SIMULATED_PE_RATIO = 15.0
SIMULATED_PB_RATIO = 1.5
SIMULATED_LOT_SIZE = 100
SIMULATED_LISTING_DATE = "2000-01-01"
SYMBOL_FIELD = Field(..., description="Security code")
QTY_FIELD = Field(..., gt=0, description="Quantity in shares")
SIDE_FIELD = Field(..., pattern="^(BUY|SELL)$", description="BUY or SELL")
PRICE_FIELD = Field(default=None, ge=0, description="Limit price if needed")
ORDER_TYPE_FIELD = Field(default="MARKET", pattern="^(MARKET|LIMIT)$")

TRADE_SIDES: dict[str, TradeSide] = {"BUY": "BUY", "SELL": "SELL"}
ORDER_TYPES: dict[str, str] = {"MARKET": OrderType.MARKET, "LIMIT": OrderType.NORMAL}
PORTFOLIO_AMOUNT_FIELDS: dict[str, tuple[str, ...]] = {
    "total_assets": ("total_assets", "power", "net_assets"),
    "market_value": ("market_val", "securities_assets"),
    "cash": ("cash", "cash_balance"),
    "available_cash": ("avl_withdrawal_cash", "available_funds", "max_power_short"),
}


def find_position_by_symbol(
    positions: list[PositionResponse], symbol: str
) -> PositionResponse | None:
    """Find a position for a specific symbol.

    Args:
        positions: List of current positions.
        symbol: Security code to find.

    Returns:
        Matching PositionResponse or None if not found.
    """
    return next((pos for pos in positions if pos.symbol == symbol), None)


@dataclass(slots=True)
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


@overload
def _extract_first[T](
    row: pd.Series, columns: tuple[str, ...], converter: Callable[[Any], T], fallback: T
) -> T: ...


@overload
def _extract_first[T](
    row: pd.Series,
    columns: tuple[str, ...],
    converter: Callable[[Any], T],
    fallback: None = None,
) -> T | None: ...


def _extract_first[T](
    row: pd.Series,
    columns: tuple[str, ...],
    converter: Callable[[Any], T],
    fallback: T | None = None,
) -> T | None:
    """Extract the first non-null candidate column of a DataFrame row.

    Args:
        row: Source DataFrame row.
        columns: Candidate column names, checked in order.
        converter: Callable applied to the first non-null cell.
        fallback: Value returned when no candidate column holds data.

    Returns:
        The converted cell value, or fallback when nothing matched.
    """
    cell = next(
        (row[column] for column in columns if column in row.index and pd.notna(row[column])),
        None,
    )
    return converter(cell) if cell is not None else fallback


def _as_int(value: Any) -> int:
    """Coerce a raw cell to int through float so fractional strings truncate."""
    return int(float(value))


def _resolve_order_type(order_type: str) -> str:
    """Resolve public order type string to Futu enum."""
    return ORDER_TYPES.get(order_type, OrderType.NORMAL)


def _resolve_order_price(resolved_order_type: str, price: float | None) -> float | None:
    """Resolve the order price, requiring one for non-market orders."""
    if resolved_order_type == OrderType.MARKET:
        return MARKET_ORDER_PRICE
    if price is None:
        raise ValueError("LIMIT orders require a price parameter to be specified")
    return price


def _stock_info_from_snapshot(payload_df: pd.DataFrame, symbol: str) -> StockInfoResponse:
    """Build a typed stock snapshot from a market snapshot payload.

    Args:
        payload_df: Snapshot payload returned by the quote context.
        symbol: Requested security code, used as symbol/name fallback.

    Returns:
        StockInfoResponse: Typed snapshot.

    Raises:
        RuntimeError: When the payload is empty or lacks a price column.
    """
    if payload_df.empty:
        raise RuntimeError(f"no market snapshot for {symbol}")
    row = payload_df.iloc[0]
    response_symbol = Extractor.extract_symbol_from_payload(payload_df, fallback=symbol)
    price = _extract_first(row, ("last_price", "nominal_price"), float)
    if price is None:
        raise RuntimeError("market snapshot missing price columns")
    return StockInfoResponse(
        symbol=response_symbol,
        name=_extract_first(row, ("name",), str, response_symbol),
        price=price,
        pe_ratio=_extract_first(row, ("pe_ratio",), float),
        pb_ratio=_extract_first(row, ("pb_ratio",), float),
        lot_size=_extract_first(row, ("lot_size",), _as_int),
        listing_date=_extract_first(row, ("list_time",), str),
    )


def _portfolio_from_row(row: pd.Series, account_id: int) -> PortfolioResponse:
    """Build a typed portfolio from an account info payload row."""
    amounts: dict[str, Any] = {
        field: _extract_first(row, columns, float, 0.0) or 0.0
        for field, columns in PORTFOLIO_AMOUNT_FIELDS.items()
    }
    amounts.update(
        unrealized_pnl=_extract_first(row, ("unrealized_pl", "holding_pl"), float),
        realized_pnl=_extract_first(row, ("realized_pl",), float),
    )
    return PortfolioResponse(account_id=account_id, **amounts)


def _position_from_row(row: pd.Series) -> PositionResponse:
    """Build a typed position response from raw DataFrame row."""
    return PositionResponse(
        symbol=_extract_first(row, ("code", "stock_code"), str, "UNKNOWN"),
        quantity=_extract_first(row, ("qty",), _as_int, 0) or 0,
        can_sell_qty=_extract_first(row, ("can_sell_qty",), _as_int, 0) or 0,
        avg_cost=_extract_first(row, ("cost_price", "cost_price_valid"), float, 0.0) or 0.0,
        market_value=_extract_first(row, ("market_val",), float, 0.0) or 0.0,
        nominal_price=_extract_first(row, ("nominal_price", "last_price"), float, 0.0) or 0.0,
        unrealized_pnl=_extract_first(row, ("pl_val", "unrealized_pl"), float),
    )


def _order_status_from_row(row: pd.Series) -> OrderStatusResponse:
    """Build a typed order status from raw DataFrame row."""
    symbol = _extract_first(row, ("code", "stock_code"), str, "UNKNOWN")
    order_id = _extract_first(row, ("order_id",), str, "")
    status = _extract_first(row, ("order_status", "status"), str, "UNKNOWN")
    if not order_id:
        raise RuntimeError("order payload missing required field: order_id")
    raw_side = _extract_first(row, ("trd_side", "side"), str)
    return OrderStatusResponse(
        order_id=order_id,
        status=status,
        symbol=symbol,
        order_side=TRADE_SIDES.get(raw_side or ""),
        qty=_extract_first(row, ("qty",), _as_int, 0) or 0,
        dealt_qty=_extract_first(row, ("dealt_qty",), _as_int, 0) or 0,
        price=_extract_first(row, ("price",), float),
        avg_fill_price=_extract_first(row, ("dealt_avg_price", "avg_price"), float),
    )


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
        self._simulated_prices: dict[str, float] = {}

    @property
    def is_connected(self) -> bool:
        """Check if client is connected to OpenD gateway."""
        return self._connected or self._paper_fallback

    def _resolve_acc_id(self) -> int:
        """Resolve account ID, falling back to DEFAULT_ACCOUNT_ID."""
        return DEFAULT_ACCOUNT_ID if self.acc_id is None else self.acc_id

    async def __aenter__(self) -> FutuClient:
        """Open contexts and start heartbeat."""
        await self.connect()
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Close contexts and stop heartbeat."""
        await self.close()

    async def connect(self) -> None:
        """Connect with bounded retries and jitter.

        Raises:
            ConnectionError: When the gateway is unreachable and paper fallback
                is disabled.
        """
        if self.allow_paper_fallback and not await self.probe_gateway():
            self._paper_fallback = True
            return
        if await self._open_contexts_with_retries():
            return
        if self.allow_paper_fallback:
            self._paper_fallback = True
            return
        raise ConnectionError("failed to connect")

    async def probe_gateway(self) -> bool:
        """Probe the OpenD TCP port, reporting whether it accepted a connection."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.connection_timeout_s,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, TimeoutError):
            return False

    async def verify_handshake(self, timeout_s: float = 5.0) -> bool:
        """Verify the futu protocol handshake within a bounded time budget.

        Uses an async-connect context so a dead or non-OpenD listener can never
        block the caller the way the synchronous constructor's reconnect loop does.

        Args:
            timeout_s: Maximum seconds to wait for the context to become READY.

        Returns:
            bool: True when the handshake completed within the budget.
        """
        try:
            ctx = await asyncio.to_thread(
                OpenQuoteContext, self.host, self.port, is_async_connect=True
            )
        except Exception:
            return False
        deadline = time.monotonic() + timeout_s
        try:
            while time.monotonic() < deadline:
                if ctx.status == ContextStatus.READY:
                    return True
                await asyncio.sleep(0.05)
            return False
        except Exception:
            return False
        finally:
            await asyncio.to_thread(ctx.close)

    async def _open_contexts_with_retries(self) -> bool:
        """Open quote/trade contexts with bounded retries, reporting success."""
        for attempt in range(self.max_retries):
            try:
                self._quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
                self._trade_ctx = OpenSecTradeContext(
                    filter_trdmarket=self.trade_market, host=self.host, port=self.port
                )
                _ = Payload.str_payload(await asyncio.to_thread(self._quote_ctx.get_global_state))
                self._connected = True
                return True
            except RuntimeError:
                await asyncio.to_thread(self._close_contexts)
                await asyncio.sleep(min(2**attempt, 10) + random.random())
        return False

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
            return self._simulated_stock_info(symbol)
        payload_df = Payload.df_payload(
            await asyncio.to_thread(self._quote_ctx.get_market_snapshot, [symbol])
        )
        return _stock_info_from_snapshot(payload_df, symbol)

    def _simulated_stock_info(self, symbol: str) -> StockInfoResponse:
        """Build the paper-mode snapshot for a symbol."""
        return StockInfoResponse(
            symbol=symbol,
            name=symbol,
            price=self._advance_simulated_price(symbol),
            pe_ratio=SIMULATED_PE_RATIO,
            pb_ratio=SIMULATED_PB_RATIO,
            lot_size=SIMULATED_LOT_SIZE,
            listing_date=SIMULATED_LISTING_DATE,
        )

    def _advance_simulated_price(self, symbol: str) -> float:
        """Advance a symbol's paper-mode price with a mean-reverting random walk."""
        price = self._simulated_prices.setdefault(symbol, SIMULATED_BASE_PRICE)
        mean_reversion = SIMULATED_MEAN_REVERSION * (SIMULATED_BASE_PRICE - price)
        price += mean_reversion + SIMULATED_VOLATILITY * random.gauss(0, 1)
        self._simulated_prices[symbol] = price
        return price

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
        resolved_order_type = _resolve_order_type(order_type)
        resolved_price = _resolve_order_price(resolved_order_type, price)
        if self._paper_fallback or self._trade_ctx is None:
            return self._simulated_order(symbol, side, qty, resolved_price)
        return await self._submit_order(
            self._trade_ctx, symbol, qty, side, resolved_order_type, resolved_price
        )

    def _simulated_order(
        self, symbol: str, side: TradeSide, qty: int, price: float | None
    ) -> OrderResponse:
        """Build the paper-mode acknowledgement for an order."""
        return OrderResponse(
            order_id=f"{symbol}-{side}-{qty}",
            status="SUBMITTED",
            symbol=symbol,
            order_side=side,
            qty=qty,
            price=price,
        )

    async def _submit_order(
        self,
        trade_ctx: OpenSecTradeContext,
        symbol: str,
        qty: int,
        side: TradeSide,
        resolved_order_type: str,
        resolved_price: float | None,
    ) -> OrderResponse:
        """Send an order through the trade context and parse the acknowledgement."""
        payload_df = Payload.df_payload(
            await asyncio.to_thread(
                trade_ctx.place_order,
                resolved_price,
                qty,
                symbol,
                side,
                order_type=resolved_order_type,
                trd_env=self.trd_env,
                acc_id=self._resolve_acc_id(),
            )
        )
        if payload_df.empty:
            raise RuntimeError("empty order response")
        order_id = Extractor.extract_row_value(payload_df, ("order_id",), f"{symbol}-{side}-{qty}")
        status = Extractor.extract_row_value(payload_df, ("order_status",), "SUBMITTED")
        return OrderResponse(
            order_id=order_id,
            status=status,
            symbol=symbol,
            order_side=side,
            qty=qty,
            price=resolved_price,
        )

    async def list_orders(self, symbol: str | None = None) -> list[OrderStatusResponse]:
        """List current account orders."""
        await self._bucket.acquire()
        await self._ensure_connected()
        if self._paper_fallback or self._trade_ctx is None:
            return self._simulated_orders(symbol)
        payload_df = Payload.df_payload(
            await asyncio.to_thread(
                self._trade_ctx.order_list_query,
                trd_env=self.trd_env,
                acc_id=self._resolve_acc_id(),
            )
        )
        if symbol is not None and "code" in payload_df.columns:
            payload_df = payload_df[payload_df["code"] == symbol]
        return [_order_status_from_row(row) for _, row in payload_df.iterrows()]

    def _simulated_orders(self, symbol: str | None) -> list[OrderStatusResponse]:
        """Build the paper-mode order history for a symbol."""
        if symbol is None:
            return []
        sim_price = self._simulated_prices.get(symbol, SIMULATED_BASE_PRICE)
        return [
            OrderStatusResponse(
                order_id=f"{symbol}-BUY-1",
                status="FILLED",
                symbol=symbol,
                order_side="BUY",
                qty=1,
                dealt_qty=1,
                price=sim_price,
                avg_fill_price=sim_price,
            )
        ]

    async def get_order_status(self, order_id: str) -> OrderStatusResponse:
        """Get one order status by order id."""
        orders = await self.list_orders()
        order = next((candidate for candidate in orders if candidate.order_id == order_id), None)
        if order is None:
            raise RuntimeError(f"order not found: {order_id}")
        return order

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
                acc_id=self._resolve_acc_id(),
            )
        )
        return [_position_from_row(row) for _, row in payload_df.iterrows()]

    async def get_portfolio(self) -> PortfolioResponse:
        """Get current account portfolio condition."""
        await self._bucket.acquire()
        await self._ensure_connected()
        account_id = self._resolve_acc_id()
        if self._paper_fallback or self._trade_ctx is None:
            return self._simulated_portfolio(account_id)
        payload_df = Payload.df_payload(
            await asyncio.to_thread(
                self._trade_ctx.accinfo_query,
                trd_env=self.trd_env,
                acc_id=account_id,
            )
        )
        if payload_df.empty:
            raise RuntimeError("empty account info response")
        return _portfolio_from_row(payload_df.iloc[0], account_id)

    def _simulated_portfolio(self, account_id: int) -> PortfolioResponse:
        """Build the paper-mode account snapshot."""
        return PortfolioResponse(
            account_id=account_id,
            total_assets=SIMULATED_STARTING_CASH,
            market_value=0.0,
            cash=SIMULATED_STARTING_CASH,
            available_cash=SIMULATED_STARTING_CASH,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
        )

    async def get_portfolio_condition(self) -> PortfolioConditionResponse:
        """Get combined account and position condition."""
        portfolio, positions = await asyncio.gather(self.get_portfolio(), self.get_positions())
        return PortfolioConditionResponse(portfolio=portfolio, positions=positions)
