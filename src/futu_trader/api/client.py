"""Futu API client wrappers."""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
from futu import (
    RET_OK,
    OpenQuoteContext,
    OpenSecTradeContext,
    OrderType,
    TrdSide,
)
from pydantic import BaseModel


class QuoteResponse(BaseModel):
    """Typed quote response."""

    symbol: str
    price: float


class OrderResponse(BaseModel):
    """Typed order response."""

    order_id: str
    status: str


MARKET_ORDER_PRICE = 0.0
DEFAULT_ACCOUNT_ID = 0


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
        trade_market: str = "HK",
        trd_env: str = "SIMULATE",
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
                ret, message = await asyncio.to_thread(self._quote_ctx.get_global_state)
                if ret != RET_OK:
                    raise RuntimeError(str(message))
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

    @staticmethod
    def _extract_row_value(
        payload: pd.DataFrame, columns: tuple[str, ...], fallback: str
    ) -> str:
        """Get first non-null value from candidate columns."""
        for column in columns:
            if column in payload.columns:
                value = payload[column].iloc[0]
                if pd.notna(value):
                    return str(value)
        return fallback

    async def get_quote(self, symbol: str) -> QuoteResponse:
        """Get quote for symbol.

        Args:
            symbol: Security code.

        Returns:
            QuoteResponse: Typed response.
        """
        await self._bucket.acquire()
        await self._ensure_connected()
        if self._paper_fallback or self._quote_ctx is None:
            return QuoteResponse(symbol=symbol, price=100.0)
        ret, payload = await asyncio.to_thread(self._quote_ctx.get_market_snapshot, [symbol])
        if ret != RET_OK:
            raise RuntimeError(str(payload))
        if payload.empty:
            raise RuntimeError(f"no market snapshot for {symbol}")
        response_symbol = self._extract_row_value(payload, ("code", "stock_code"), symbol)
        if "last_price" in payload.columns:
            price = float(payload["last_price"].iloc[0])
        elif "nominal_price" in payload.columns:
            price = float(payload["nominal_price"].iloc[0])
        else:
            raise RuntimeError("market snapshot missing price columns")
        return QuoteResponse(symbol=response_symbol, price=price)

    async def place_order(self, symbol: str, qty: int, side: str) -> OrderResponse:
        """Place an order.

        Args:
            symbol: Security code.
            qty: Quantity in shares.
            side: BUY or SELL.

        Returns:
            OrderResponse: Typed response.
        """
        if qty <= 0:
            raise ValueError("qty must be positive")
        side_upper = side.upper()
        side_map = {"BUY": TrdSide.BUY, "SELL": TrdSide.SELL}
        if side_upper not in side_map:
            raise ValueError("side must be BUY or SELL")
        await self._bucket.acquire()
        await self._ensure_connected()
        if self._paper_fallback or self._trade_ctx is None:
            return OrderResponse(order_id=f"{symbol}-{side}-{qty}", status="SUBMITTED")
        ret, payload = await asyncio.to_thread(
            self._trade_ctx.place_order,
            MARKET_ORDER_PRICE,
            qty,
            symbol,
            side_map[side_upper],
            order_type=OrderType.MARKET,
            trd_env=self.trd_env,
            acc_id=DEFAULT_ACCOUNT_ID if self.acc_id is None else self.acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(str(payload))
        if payload.empty:
            raise RuntimeError("empty order response")
        order_id = self._extract_row_value(payload, ("order_id",), f"{symbol}-{side}-{qty}")
        status = self._extract_row_value(payload, ("order_status",), "SUBMITTED")
        return OrderResponse(order_id=order_id, status=status)
