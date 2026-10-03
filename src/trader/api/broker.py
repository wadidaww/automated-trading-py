"""Broker client interface shared by equity and crypto clients.

``OrderManager``, ``RiskStage``, ``QuotePoller``, the runtime and the health check depend on this
protocol, not on a concrete client, so a market can be added by registering a new client in
``trader.api.factory`` without touching them.
"""

from __future__ import annotations

from typing import Any, Protocol

from trader.api.models import (
    OrderResponse,
    OrderStatusResponse,
    PortfolioConditionResponse,
    PortfolioResponse,
    PositionResponse,
    QuoteResponse,
    StockInfoResponse,
)
from trader.misc.types.futu import TradeSide


class BrokerClient(Protocol):
    """Async trade/quote client for one market."""

    trd_env: str

    @property
    def is_connected(self) -> bool:
        """Whether the client is connected (or running on the paper simulator)."""
        ...

    async def __aenter__(self) -> BrokerClient: ...

    async def __aexit__(self, *_: Any) -> None: ...

    async def probe_gateway(self) -> bool:
        """Whether the OpenD TCP port accepts a connection."""
        ...

    async def verify_handshake(self, timeout_s: float = 5.0) -> bool:
        """Whether the futu protocol handshake completes within ``timeout_s``."""
        ...

    async def get_quote(self, symbol: str) -> QuoteResponse:
        """Last price for ``symbol``."""
        ...

    async def get_stock_info(self, symbol: str) -> StockInfoResponse:
        """Snapshot including the instrument metadata order validity needs."""
        ...

    async def place_order(
        self,
        symbol: str,
        qty: float,
        side: TradeSide,
        order_type: str = "MARKET",
        price: float | None = None,
        remark: str | None = None,
    ) -> OrderResponse:
        """Send an order; ``remark`` carries the client order id."""
        ...

    async def list_orders(self, symbol: str | None = None) -> list[OrderStatusResponse]:
        """Current account orders."""
        ...

    async def get_order_status(self, order_id: str) -> OrderStatusResponse:
        """One order by broker order id."""
        ...

    async def get_positions(self) -> list[PositionResponse]:
        """Current positions."""
        ...

    async def get_portfolio(self) -> PortfolioResponse:
        """Account funds."""
        ...

    async def get_portfolio_condition(self) -> PortfolioConditionResponse:
        """Account funds and positions together."""
        ...

    async def verify_account(self) -> None:
        """Raise unless the configured account exists for this ``trd_env``."""
        ...

    async def unlock_trade(self, password_md5: str) -> None:
        """Unlock trading for REAL orders."""
        ...

    async def cancel_all_orders(self) -> None:
        """Cancel every working order (kill switch / cancel-on-exit)."""
        ...
