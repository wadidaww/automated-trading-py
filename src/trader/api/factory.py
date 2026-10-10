"""Client factory: pick the broker client for ``trading.market``.

A registry keyed by market (like the model registry in the pipeline factory). Markets without an
entry get the equity ``FutuClient``. Register a builder to add a market.
"""

from __future__ import annotations

from collections.abc import Callable

from futu import TrdEnv

from trader.api.broker import BrokerClient
from trader.api.client import FutuClient
from trader.api.crypto_client import FutuCryptoClient
from trader.core.symbols import CRYPTO_MARKET
from trader.utils.config import AppConfig

type ClientBuilder = Callable[[AppConfig, str], BrokerClient]


def _common_kwargs(config: AppConfig, mode: str) -> dict[str, object]:
    """Connection and account arguments shared by every Futu-backed client."""
    account_id = config.trading.account_id
    return {
        "host": config.opend.host,
        "port": config.opend.port,
        "max_retries": config.opend.reconnect_max_attempts,
        "heartbeat_interval_s": config.opend.heartbeat_interval_s,
        "rate_limit_requests": config.opend.rate_limit_requests,
        "rate_limit_window_s": config.opend.rate_limit_window_s,
        "trd_env": TrdEnv.REAL if mode == "live" else TrdEnv.SIMULATE,
        "acc_id": int(account_id) if account_id.isdigit() else None,
        "allow_paper_fallback": mode != "live",
    }


def _build_equity_client(config: AppConfig, mode: str) -> BrokerClient:
    """Equity client (HK/US/...) on ``OpenSecTradeContext``."""
    return FutuClient(trade_market=config.trading.market, **_common_kwargs(config, mode))  # type: ignore[arg-type]


def _build_crypto_client(config: AppConfig, mode: str) -> BrokerClient:
    """Crypto client on ``OpenCryptoTradeContext``; paper mode uses the local simulator."""
    if config.trading.crypto is None:
        raise ValueError("market CC requires a trading.crypto block")
    return FutuCryptoClient(config.trading.crypto, **_common_kwargs(config, mode))


_CLIENT_BUILDERS: dict[str, ClientBuilder] = {CRYPTO_MARKET: _build_crypto_client}


def register_client_builder(market: str, builder: ClientBuilder) -> None:
    """Register (or replace) the client builder for ``market``."""
    _CLIENT_BUILDERS[market] = builder


def create_client(config: AppConfig, mode: str) -> BrokerClient:
    """Build the client for ``config.trading.market``.

    Live mode never falls back to the paper simulator: an unreachable gateway is an error.

    Args:
        config: Application configuration.
        mode: Trading mode (paper or live).
    """
    return _CLIENT_BUILDERS.get(config.trading.market, _build_equity_client)(config, mode)
