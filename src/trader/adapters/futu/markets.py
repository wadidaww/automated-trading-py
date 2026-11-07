"""Market profile contract for :class:`~trader.adapters.futu.client.FutuClient`.

A ``FutuMarket`` holds everything that differs between Futu markets (equities, crypto, ...):
which OpenD contexts to open, how an order is parameterised, which instrument rules a snapshot
carries and how account queries are checked. The client owns connection lifecycle, rate limiting
and response parsing, and calls the market only through this public contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import pandas as pd
    from futu import OpenQuoteContext

    from trader.adapters.sim.simulator import PaperSimulator
    from trader.ports.models import StockInfoResponse


class FutuMarket(Protocol):
    """Market-specific behaviour plugged into ``FutuClient``."""

    trade_market: str

    def new_quote_ctx(self, host: str, port: int) -> OpenQuoteContext:
        """Open the quote context."""
        ...

    def new_trade_ctx(self, host: str, port: int) -> Any:
        """Open the trade context."""
        ...

    def order_params(self, order_type: str, qty: float) -> tuple[str, float, dict[str, Any]]:
        """Broker order type, quantity to send and extra ``place_order`` kwargs.

        Raises:
            ValueError: When ``qty`` is not tradable on this market.
        """
        ...

    def with_instrument_rules(
        self, info: StockInfoResponse, *, simulated: bool
    ) -> StockInfoResponse:
        """Attach market instrument metadata (lot, tick, step, minimum) to a snapshot."""
        ...

    def prepare_query(self, method: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Extra arguments for a trade-context account query."""
        ...

    def check_query(self, method: str, frame: pd.DataFrame) -> None:
        """Validate an account query response.

        Raises:
            RuntimeError: When the response cannot be trusted.
        """
        ...

    def simulator_only(self, trd_env: str) -> bool:
        """Whether this ``trd_env`` always runs on the local simulator, never OpenD."""
        ...

    def make_simulator(self) -> PaperSimulator:
        """Paper simulator configured for this market."""
        ...
