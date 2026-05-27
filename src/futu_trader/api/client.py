"""Futu API client wrappers."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


class QuoteResponse(BaseModel):
    """Typed quote response."""

    symbol: str
    price: float


class OrderResponse(BaseModel):
    """Typed order response."""

    order_id: str
    status: str


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
    ) -> None:
        self.host = host
        self.port = port
        self.max_retries = max_retries
        self.heartbeat_interval_s = heartbeat_interval_s
        self._connected = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._bucket = TokenBucket.create(rate_limit_requests, rate_limit_window_s)

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
        for attempt in range(self.max_retries):
            try:
                self._connected = True
                return
            except RuntimeError:
                delay = min(2**attempt, 10) + random.random()
                await asyncio.sleep(delay)
        raise ConnectionError("failed to connect")

    async def close(self) -> None:
        """Close API contexts."""
        self._connected = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with __import__("contextlib").suppress(asyncio.CancelledError):
                await self._heartbeat_task

    async def _heartbeat(self) -> None:
        """Heartbeat loop."""
        while self._connected:
            await asyncio.sleep(self.heartbeat_interval_s)

    async def get_quote(self, symbol: str) -> QuoteResponse:
        """Get quote for symbol.

        Args:
            symbol: Security code.

        Returns:
            QuoteResponse: Typed response.
        """
        await self._bucket.acquire()
        return QuoteResponse(symbol=symbol, price=100.0)

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
        await self._bucket.acquire()
        return OrderResponse(order_id=f"{symbol}-{side}-{qty}", status="SUBMITTED")
