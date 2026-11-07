"""Futu crypto client: ``OpenCryptoTradeContext``, fractional quantities, GTC/IOC, 24/7.

Differences from the equity client that this module encodes (see Futu's crypto API limits):

* Crypto trades REAL only. There is no Futu SIMULATE for it, so any non-REAL run uses the local
  ``PaperSimulator`` and never touches OpenD for trading.
* Orders are LIMIT (``NORMAL``, GTC) or MARKET (IOC). Orders cannot be modified, only cancelled.
* Quantities are decimals on a per-pair step; there are no board lots.
* Quote and trade contexts need a ``security_firm`` that offers crypto accounts.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from futu import OpenQuoteContext, OrderType, SecurityFirm, TimeInForce, TrdEnv

try:  # Added in futu-api 10.5.6508; the repo's 9.x pin does not ship it.
    from futu import OpenCryptoTradeContext
except ImportError:  # pragma: no cover - depends on the installed SDK
    OpenCryptoTradeContext = None  # type: ignore[assignment,misc]

from trader.adapters.futu.client import FutuClient
from trader.ports.models import StockInfoResponse
from trader.adapters.sim.simulator import PaperSimulator
from trader.core.symbols import CRYPTO_MARKET
from trader.utils.config import CRYPTO_SECURITY_FIRMS, CryptoSettings

# Paper prices wander about 0.2% per step around the base price.
_PAPER_VOLATILITY_PCT = 0.002

# Futu's wire values; older SDKs omit some TimeInForce members, so fall back to the literal.
_GTC = getattr(TimeInForce, "GTC", "GTC")
_IOC = getattr(TimeInForce, "IOC", "IOC")

_CURRENCY_QUERIES = frozenset({"accinfo_query", "position_list_query"})

MIN_SDK_MESSAGE = (
    "crypto trading needs futu-api >= 10.5.6508 (OpenCryptoTradeContext); "
    "run: pip install --upgrade 'futu-api>=10.5.6508'"
)

__all__ = ["CryptoMarket", "CryptoSdkError", "FutuCryptoClient"]


class CryptoSdkError(Exception):
    """The installed futu-api cannot do crypto.

    Deliberately not a RuntimeError: connect retries would swallow it.
    """


class CryptoMarket:
    """Futu crypto pairs such as ``CC.BTCUSD`` on ``OpenCryptoTradeContext``."""

    trade_market = CRYPTO_MARKET

    def __init__(self, settings: CryptoSettings) -> None:
        self.settings = settings

    def simulator_only(self, trd_env: str) -> bool:
        """Every non-REAL run trades on the local simulator: Futu has no crypto SIMULATE."""
        return trd_env != TrdEnv.REAL

    def make_simulator(self) -> PaperSimulator:
        """Simulator around the configured paper price, without board lots."""
        return PaperSimulator(
            base_price=self.settings.paper_base_price,
            volatility=self.settings.paper_base_price * _PAPER_VOLATILITY_PCT,
            starting_cash=self.settings.paper_cash,
            lot_size=None,
        )

    def prepare_query(self, method: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Query account and positions in the configured account currency.

        Futu defaults ``accinfo_query`` to HKD and positions to USD; mixing them would scale the
        portfolio value (and every concentration check) by the FX rate.
        """
        if method in _CURRENCY_QUERIES:
            return {"currency": self.settings.currency, **kwargs}
        return kwargs

    def check_query(self, method: str, frame: pd.DataFrame) -> None:
        """Refuse a response reported in any currency but the configured one.

        Raises:
            RuntimeError: When a returned row reports a different currency.
        """
        currency = self.settings.currency
        if "currency" in frame.columns:
            other = {str(c) for c in frame["currency"].dropna()} - {currency}
            if other:
                raise RuntimeError(f"{method} returned {sorted(other)}, expected {currency}")

    def _security_firm(self) -> str:
        """The configured brokerage as a futu ``SecurityFirm`` value.

        Raises:
            ValueError: When no crypto-capable brokerage is configured.
        """
        name = self.settings.security_firm
        if name not in CRYPTO_SECURITY_FIRMS:
            raise ValueError(
                "crypto trading requires trading.crypto.security_firm "
                f"(one of {sorted(CRYPTO_SECURITY_FIRMS)}; env FUTU_SECURITY_FIRM)"
            )
        return str(getattr(SecurityFirm, name))

    def new_quote_ctx(self, host: str, port: int) -> OpenQuoteContext:
        """Quote context bound to the crypto brokerage."""
        try:
            return OpenQuoteContext(host=host, port=port, security_firm=self._security_firm())
        except TypeError as exc:  # futu-api < 10: no security_firm parameter
            raise CryptoSdkError(MIN_SDK_MESSAGE) from exc

    def new_trade_ctx(self, host: str, port: int) -> Any:
        """Crypto trade context bound to the crypto brokerage.

        Raises:
            CryptoSdkError: When the installed futu-api predates crypto support.
        """
        if OpenCryptoTradeContext is None:
            raise CryptoSdkError(MIN_SDK_MESSAGE)
        return OpenCryptoTradeContext(host=host, port=port, security_firm=self._security_firm())

    def order_params(self, order_type: str, qty: float) -> tuple[str, float, dict[str, Any]]:
        """LIMIT → NORMAL/GTC, MARKET → MARKET/IOC; quantity must reach ``min_qty``.

        Raises:
            ValueError: When ``qty`` is below the pair's minimum.
        """
        if qty < self.settings.min_qty:
            raise ValueError(f"crypto quantity {qty} is below min_qty {self.settings.min_qty}")
        if order_type == "MARKET":
            return OrderType.MARKET, float(qty), {"time_in_force": _IOC}
        return OrderType.NORMAL, float(qty), {"time_in_force": _GTC}

    def with_instrument_rules(
        self, info: StockInfoResponse, *, simulated: bool
    ) -> StockInfoResponse:
        """Attach the crypto tick, step and minimum; crypto has no board lot.

        The live tick comes from the broker snapshot (``price_spread``) and is left unset when
        the broker gives none, so the risk stage rejects instead of guessing. The simulator has
        no snapshot, so paper runs use the configured tick.
        """
        tick = self.settings.paper_price_tick if simulated else info.tick_size
        return info.model_copy(
            update={
                "lot_size": None,
                "tick_size": tick,
                "qty_step": self.settings.qty_step,
                "min_qty": self.settings.min_qty,
            }
        )


class FutuCryptoClient(FutuClient):
    """``FutuClient`` on a :class:`CryptoMarket`; a REAL run never falls back to paper."""

    def __init__(self, settings: CryptoSettings, **kwargs: Any) -> None:
        kwargs.setdefault("trd_env", TrdEnv.SIMULATE)
        # A REAL crypto run must never quietly degrade into the simulator.
        if kwargs["trd_env"] == TrdEnv.REAL:
            kwargs["allow_paper_fallback"] = False
        super().__init__(market=CryptoMarket(settings), **kwargs)
        self.settings = settings

    @property
    def simulated(self) -> bool:
        """Whether this client trades on the local simulator (every non-REAL run)."""
        return self.market.simulator_only(self.trd_env)
