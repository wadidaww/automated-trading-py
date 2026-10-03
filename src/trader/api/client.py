"""Futu OpenD client: connection lifecycle, rate limiting and typed trade/quote calls."""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from typing import Any

import pandas as pd
from futu import OpenQuoteContext, OpenSecTradeContext, OrderType, TrdEnv, TrdMarket
from futu.common.constant import ContextStatus
from pydantic import Field, validate_call

from trader.api.models import (
    OrderResponse,
    OrderStatusResponse,
    PortfolioConditionResponse,
    PortfolioResponse,
    PositionResponse,
    QuoteResponse,
    StockInfoResponse,
    find_position_by_symbol,
)
from trader.api.parsers import (
    order_status_from_row,
    portfolio_from_row,
    position_from_row,
    stock_info_from_snapshot,
)
from trader.api.rate_limit import TokenBucket
from trader.api.simulator import PaperSimulator
from trader.api.typesafe.payload import Payload
from trader.misc.types.futu import TradeSide
from trader.utils.dataframe import first_value

__all__ = [
    "FutuClient",
    "OrderResponse",
    "OrderStatusResponse",
    "PortfolioConditionResponse",
    "PortfolioResponse",
    "PositionResponse",
    "QuoteResponse",
    "StockInfoResponse",
    "find_position_by_symbol",
]

MARKET_ORDER_PRICE = 0.0
DEFAULT_ACCOUNT_ID = 0
SYMBOL_FIELD = Field(..., description="Security code")
QTY_FIELD = Field(..., gt=0, description="Quantity in shares")
SIDE_FIELD = Field(..., pattern="^(BUY|SELL)$", description="BUY or SELL")
PRICE_FIELD = Field(default=None, ge=0, description="Limit price if needed")
ORDER_TYPE_FIELD = Field(default="MARKET", pattern="^(MARKET|LIMIT)$")
REMARK_FIELD = Field(default=None, max_length=64, description="Client order id (Futu remark)")

ORDER_TYPES: dict[str, str] = {"MARKET": OrderType.MARKET, "LIMIT": OrderType.NORMAL}


def _resolve_order_price(resolved_order_type: str, price: float | None) -> float | None:
    """Resolve the order price, requiring one for non-market orders."""
    if resolved_order_type == OrderType.MARKET:
        return MARKET_ORDER_PRICE
    if price is None:
        raise ValueError("LIMIT orders require a price parameter to be specified")
    return price


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
        self._bucket = TokenBucket(rate_limit_requests, rate_limit_window_s)
        self._quote_ctx: OpenQuoteContext | None = None
        self._trade_ctx: OpenSecTradeContext | None = None
        self._simulator = PaperSimulator()

    @property
    def is_connected(self) -> bool:
        """Check if client is connected to OpenD gateway."""
        return self._connected or self._paper_fallback

    def _resolve_acc_id(self) -> int:
        """Resolve account ID.

        SIMULATE falls back to DEFAULT_ACCOUNT_ID (Futu's first simulated account). REAL never
        falls back: trading an account nobody named is refused.

        Raises:
            ValueError: When trading REAL without an explicit numeric account id.
        """
        if self.acc_id is not None:
            return self.acc_id
        if self.trd_env == TrdEnv.REAL:
            raise ValueError("REAL trading requires an explicit numeric acc_id")
        return DEFAULT_ACCOUNT_ID

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

    async def _ready(self) -> None:
        """Rate-limit, then make sure contexts are connected before a request."""
        await self._bucket.acquire()
        await self._ensure_connected()

    async def _trade_query(self, method: str, **kwargs: Any) -> pd.DataFrame | None:
        """Call a trade-context query for this account; None when running on the simulator."""
        trade_ctx = self._trade_ctx
        if self._paper_fallback or trade_ctx is None:
            return None
        return Payload.df_payload(
            await asyncio.to_thread(
                getattr(trade_ctx, method),
                trd_env=self.trd_env,
                acc_id=self._resolve_acc_id(),
                **kwargs,
            )
        )

    async def get_quote(self, symbol: str) -> QuoteResponse:
        """Get the last price for a symbol."""
        stock_info = await self.get_stock_info(symbol)
        return QuoteResponse(symbol=stock_info.symbol, price=stock_info.price)

    async def get_stock_info(self, symbol: str) -> StockInfoResponse:
        """Get stock snapshot with valuation metrics."""
        await self._ready()
        if self._paper_fallback or self._quote_ctx is None:
            return self._simulator.stock_info(symbol)
        payload_df = Payload.df_payload(
            await asyncio.to_thread(self._quote_ctx.get_market_snapshot, [symbol])
        )
        return stock_info_from_snapshot(payload_df, symbol)

    @validate_call
    async def place_order(
        self,
        symbol: str = SYMBOL_FIELD,
        qty: int = QTY_FIELD,
        side: TradeSide = SIDE_FIELD,
        order_type: str = ORDER_TYPE_FIELD,
        price: float | None = PRICE_FIELD,
        remark: str | None = REMARK_FIELD,
    ) -> OrderResponse:
        """Place an order.

        Args:
            symbol: Security code.
            qty: Quantity in shares.
            side: BUY or SELL.
            order_type: MARKET or LIMIT.
            price: Limit price (required for LIMIT orders).
            remark: Client order id echoed back by Futu, used for reconciliation.
        """
        await self._ready()
        resolved_order_type = ORDER_TYPES.get(order_type, OrderType.NORMAL)
        resolved_price = _resolve_order_price(resolved_order_type, price)
        trade_ctx = self._trade_ctx
        if self._paper_fallback or trade_ctx is None:
            return self._simulator.order_ack(symbol, side, qty, resolved_price, remark)
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
                remark=remark,
            )
        )
        if payload_df.empty:
            raise RuntimeError("empty order response")
        ack = payload_df.iloc[0]
        order_id = first_value(ack, ("order_id",), str, "")
        if not order_id:
            raise RuntimeError("order response missing order_id")
        return OrderResponse(
            order_id=order_id,
            # No status in the ack means we do not know yet: UNKNOWN keeps exposure counted.
            status=first_value(ack, ("order_status",), str, "UNKNOWN"),
            symbol=symbol,
            order_side=side,
            qty=qty,
            price=resolved_price,
        )

    async def list_orders(self, symbol: str | None = None) -> list[OrderStatusResponse]:
        """List current account orders."""
        await self._ready()
        payload_df = await self._trade_query("order_list_query")
        if payload_df is None:
            return self._simulator.orders(symbol)
        if symbol is not None and "code" in payload_df.columns:
            payload_df = payload_df[payload_df["code"] == symbol]
        return [order_status_from_row(row) for _, row in payload_df.iterrows()]

    async def get_order_status(self, order_id: str) -> OrderStatusResponse:
        """Get one order status by order id."""
        orders = await self.list_orders()
        order = next((candidate for candidate in orders if candidate.order_id == order_id), None)
        if order is None:
            raise RuntimeError(f"order not found: {order_id}")
        return order

    async def get_positions(self) -> list[PositionResponse]:
        """Get current live positions."""
        await self._ready()
        payload_df = await self._trade_query("position_list_query")
        if payload_df is None:
            return []
        return [position_from_row(row) for _, row in payload_df.iterrows()]

    async def get_portfolio(self) -> PortfolioResponse:
        """Get current account portfolio condition."""
        await self._ready()
        account_id = self._resolve_acc_id()
        payload_df = await self._trade_query("accinfo_query")
        if payload_df is None:
            return self._simulator.portfolio(account_id)
        if payload_df.empty:
            raise RuntimeError("empty account info response")
        return portfolio_from_row(payload_df.iloc[0], account_id)

    async def verify_account(self) -> None:
        """Check that ``acc_id`` is one of this login's accounts for ``trd_env``.

        Raises:
            RuntimeError: When not connected to OpenD or the account is not listed.
        """
        acc_id = self._resolve_acc_id()
        if self._paper_fallback or self._trade_ctx is None:
            raise RuntimeError("cannot verify account without an OpenD trade context")
        payload_df = Payload.df_payload(await asyncio.to_thread(self._trade_ctx.get_acc_list))
        listed = {(int(row["acc_id"]), str(row["trd_env"])) for _, row in payload_df.iterrows()}
        if (acc_id, str(self.trd_env)) not in listed:
            raise RuntimeError(f"acc_id {acc_id} not found for trd_env {self.trd_env}")

    async def unlock_trade(self, password_md5: str) -> None:
        """Unlock trading for REAL orders with the MD5 of the trade password.

        Raises:
            RuntimeError: When not connected or OpenD refuses the unlock.
        """
        if self._paper_fallback or self._trade_ctx is None:
            raise RuntimeError("cannot unlock trade without an OpenD trade context")
        Payload.check(
            await asyncio.to_thread(
                self._trade_ctx.unlock_trade, password_md5=password_md5, is_unlock=True
            )
        )

    async def cancel_all_orders(self) -> None:
        """Cancel every working order on the account (kill switch / cancel-on-exit)."""
        await self._ensure_connected()
        if self._paper_fallback or self._trade_ctx is None:
            return
        Payload.check(
            await asyncio.to_thread(
                self._trade_ctx.cancel_all_order,
                trd_env=self.trd_env,
                acc_id=self._resolve_acc_id(),
            )
        )

    async def get_portfolio_condition(self) -> PortfolioConditionResponse:
        """Get combined account and position condition."""
        portfolio, positions = await asyncio.gather(self.get_portfolio(), self.get_positions())
        return PortfolioConditionResponse(portfolio=portfolio, positions=positions)
