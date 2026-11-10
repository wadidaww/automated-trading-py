"""Configuration loading helpers."""

from __future__ import annotations

import math
import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trader.core.symbols import CRYPTO_MARKET, market_of, normalize_symbol

# Futu documents ~15 place_order calls per 30 s per account; stay strictly below it.
FUTU_MAX_ORDERS_PER_30S = 15


class OpendConfig(BaseModel):
    """OpenD gateway config."""

    host: str
    port: int
    heartbeat_interval_s: int = 10
    reconnect_max_attempts: int = 5
    rate_limit_requests: int = 300
    rate_limit_window_s: int = 30
    # Name of the env var holding the MD5 of the trade password (REAL only). Never the value.
    unlock_password_md5_env: str = "FUTU_TRADE_PWD_MD5"  # noqa: S105 - env var name


# ``model.type`` of the trend model, which states target positions and needs ``risk.vol_target``.
TREND_MODEL_TYPE = "trend"
DAILY_INTERVAL = "1d"
DONCHIAN_WINDOW_MULTIPLE = 2
EQUITY_CALENDAR_DAYS_PER_BAR = 1.5

# Futu brokerages that offer crypto accounts (OpenCryptoTradeContext ``security_firm``).
CRYPTO_SECURITY_FIRMS = frozenset({"FUTUSECURITIES", "FUTUINC", "FUTUSG"})


class CryptoSettings(BaseModel):
    """Crypto (``market: CC``) instrument and paper-trading settings.

    Futu crypto trades REAL only (no SIMULATE), so ``--mode paper`` runs on the local simulator
    seeded from the ``paper_*`` values. ``qty_step`` and ``min_qty`` must match the precision the
    account accepts for the pair; they are rounding rules, never sizes.
    """

    # Empty means "not set": a REAL crypto run refuses to start without a named brokerage.
    security_firm: str = ""
    # Account currency for funds/position queries; a response in another currency is refused.
    currency: str = "USD"
    # No defaults: a wrong step silently mis-sizes orders, so the config must state them.
    qty_step: float = Field(gt=0)
    min_qty: float = Field(gt=0)
    paper_base_price: float = Field(default=2000.0, gt=0)
    paper_price_tick: float = Field(default=0.01, gt=0)
    paper_cash: float = Field(default=100_000.0, gt=0)

    @field_validator("security_firm")
    @classmethod
    def _check_firm(cls, value: str) -> str:
        """Allow only Futu brokerages that offer crypto accounts (or unset)."""
        firm = value.strip().upper()
        if firm and firm not in CRYPTO_SECURITY_FIRMS:
            raise ValueError(f"security_firm must be one of {sorted(CRYPTO_SECURITY_FIRMS)}")
        return firm


class TradingSettings(BaseModel):
    """Trading runtime settings.

    Notional limits are in the account currency (HKD for HK, USD for US and crypto). The legacy
    ``*_hkd`` keys are still accepted.
    """

    account_id: str = Field(...)
    market: str = "HK"
    # SIMULATE or REAL. REAL additionally requires --mode live and TRADER_LIVE_CONFIRM=1.
    trd_env: Literal["SIMULATE", "REAL"] = "SIMULATE"
    symbols: list[str]
    max_position_notional: int = Field(
        validation_alias=AliasChoices("max_position_notional", "max_position_notional_hkd")
    )
    max_portfolio_notional: int = Field(
        validation_alias=AliasChoices("max_portfolio_notional", "max_portfolio_notional_hkd")
    )
    max_daily_loss: int = Field(
        validation_alias=AliasChoices("max_daily_loss", "max_daily_loss_hkd")
    )
    max_open_orders: int
    concentration_limit_pct: float
    signal_cooldown_s: int
    order_type: str = "LIMIT"
    strategy_name: str = "default"
    crypto: CryptoSettings | None = None

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: list[str]) -> list[str]:
        """Normalise every code to Futu ``MARKET.CODE`` form (``700.HK`` → ``HK.00700``)."""
        return [normalize_symbol(symbol) for symbol in value]

    @model_validator(mode="after")
    def _check_crypto_consistency(self) -> TradingSettings:
        """Crypto and equity symbols never mix: they use different clients and order rules."""
        is_crypto = self.market == CRYPTO_MARKET
        if is_crypto and self.crypto is None:
            raise ValueError("market CC requires a trading.crypto block (qty_step, min_qty)")
        if any((market_of(symbol) == CRYPTO_MARKET) != is_crypto for symbol in self.symbols):
            raise ValueError(f"market {self.market!r} cannot trade the configured symbols")
        return self


class VolTargetSettings(BaseModel):
    """Volatility-target position sizing for models that state a target exposure (trend).

    Target notional for a symbol = portfolio value x ``symbol_weight`` x the model's target
    exposure x min(1, ``target_vol_annual`` / realised volatility). Never above ``symbol_weight``:
    there is no leverage. Off by default; every other limit still applies when it is on.
    """

    enabled: bool = False
    target_vol_annual: float = Field(default=0.60, gt=0, le=2.0)
    # Largest share of the portfolio one symbol may hold under this sizing.
    symbol_weight: float = Field(default=0.10, gt=0, le=1.0)
    # Do not trade while the position is within this share of the symbol's budget of the target
    # (a full exit always trades). Stops daily dust orders.
    rebalance_band: float = Field(default=0.10, ge=0, lt=1.0)


class RiskSettings(BaseModel):
    """Per-order limits, throttle and kill switch. Money values are account-currency major units.

    Portfolio-level limits (position/portfolio notional, daily loss, open orders, concentration)
    live under ``trading``.
    """

    max_order_qty: float = Field(default=10_000, gt=0)
    max_order_notional: float = Field(default=50_000.0, gt=0)
    max_orders_per_second: int = Field(default=2, gt=0)
    max_orders_per_30s: int = Field(default=12, gt=0)
    price_band_pct: float = 0.02
    allow_short: bool = False
    kill_switch_file: str = "run/KILL"
    cancel_on_exit: bool = True
    vol_target: VolTargetSettings = Field(default_factory=VolTargetSettings)

    @model_validator(mode="after")
    def _check_limits(self) -> RiskSettings:
        """Reject configs that would exceed the broker's documented order-rate limit."""
        if self.max_orders_per_30s >= FUTU_MAX_ORDERS_PER_30S:
            raise ValueError(f"max_orders_per_30s must be < {FUTU_MAX_ORDERS_PER_30S}")
        if not 0.0 < self.price_band_pct < 0.2:
            raise ValueError("price_band_pct must be in (0, 0.2)")
        return self


class StorageSettings(BaseModel):
    """Durable state (orders, fills, audit) and market-data locations."""

    sqlite_path: str = "data/state/trader.db"
    parquet_dir: str = "data/raw"


class SessionSettings(BaseModel):
    """Exchange calendar overrides (HKT dates)."""

    holidays: list[date] = Field(default_factory=list)
    half_days: list[date] = Field(default_factory=list)
    reconcile_interval_s: int = 60


class DailySignalSettings(BaseModel):
    """Scheduled daily job: signals from the previous session's close become limit orders."""

    # Calendar days of daily bars to download; must cover ``pipeline.data_window_size`` sessions.
    history_days: int = Field(default=400, gt=0)
    ktype: str = "K_DAY"
    # Last session acted on per symbol, so a repeated cron run never sends a second order.
    state_file: str = "data/state/daily_signals.json"


class TrendSettings(BaseModel):
    """Lookbacks, in bars, for ``model.type: trend``. Defaults suit daily crypto bars.

    For HK daily bars use ``lookbacks: [63, 126, 252]`` and ``bars_per_year: 252``.
    """

    lookbacks: list[int] = Field(default_factory=lambda: [91, 182, 365], min_length=1)
    donchian_entry: int = Field(default=55, gt=1)
    donchian_exit: int = Field(default=20, gt=1)
    vol_window: int = Field(default=20, gt=1)
    bars_per_year: float = Field(default=365.0, gt=0)

    @field_validator("lookbacks")
    @classmethod
    def _check_lookbacks(cls, value: list[int]) -> list[int]:
        """Every lookback is a positive number of bars."""
        if any(lookback <= 0 for lookback in value):
            raise ValueError("model.trend.lookbacks must be positive")
        return value

    @property
    def min_points(self) -> int:
        """Prices the data window must hold before every trend feature is defined."""
        return max(*self.lookbacks, self.donchian_entry, self.donchian_exit, self.vol_window) + 1


class ModelSettings(BaseModel):
    """Model config."""

    type: str
    path: str
    hot_reload: bool
    confidence_threshold: float
    trend: TrendSettings = Field(default_factory=TrendSettings)


class PipelineSettings(BaseModel):
    """Pipeline config."""

    data_window_size: int
    feature_interval: str
    queue_maxsize: int


class LoggingSettings(BaseModel):
    """Logging config."""

    level: str
    format: str
    file: str
    rotate_bytes: int


class MetricsSettings(BaseModel):
    """Metrics config."""

    prometheus_port: int


class AppConfig(BaseSettings):
    """Validated application configuration."""

    opend: OpendConfig
    trading: TradingSettings
    model: ModelSettings
    pipeline: PipelineSettings
    logging: LoggingSettings
    metrics: MetricsSettings
    risk: RiskSettings = Field(default_factory=RiskSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    daily: DailySignalSettings = Field(default_factory=DailySignalSettings)
    model_config = SettingsConfigDict(env_file=".env")

    @model_validator(mode="after")
    def _check_trend_model(self) -> AppConfig:
        """The trend model fails at startup, not at order time, when its config cannot work."""
        if self.model.type != TREND_MODEL_TYPE:
            return self
        sizing = self.risk.vol_target
        if not sizing.enabled:
            raise ValueError("model.type 'trend' requires risk.vol_target.enabled: true")
        if sizing.symbol_weight > self.trading.concentration_limit_pct:
            raise ValueError(
                "risk.vol_target.symbol_weight must not exceed trading.concentration_limit_pct"
            )
        trend = self.model.trend
        # The Donchian state is replayed over the window from a flat start, so the window must
        # be well past the entry lookback or the vote drops when the breakout bar slides out.
        need = max(trend.min_points, DONCHIAN_WINDOW_MULTIPLE * trend.donchian_entry)
        if self.pipeline.data_window_size < need:
            raise ValueError(f"model.type 'trend' needs pipeline.data_window_size >= {need}")
        # Lookbacks are counted in daily bars; on the tick pipeline they would mean minutes.
        if self.pipeline.feature_interval != DAILY_INTERVAL:
            raise ValueError("model.type 'trend' needs pipeline.feature_interval: '1d'")
        # history_days is calendar days; equity markets trade about 5 days in 7, with holidays.
        per_bar = 1.0 if self.trading.market == CRYPTO_MARKET else EQUITY_CALENDAR_DAYS_PER_BAR
        days = math.ceil(self.pipeline.data_window_size * per_bar)
        if self.daily.history_days < days:
            raise ValueError(
                f"daily.history_days must be >= {days} to cover pipeline.data_window_size"
            )
        return self


def load_config(path: str) -> AppConfig:
    """Load YAML config file with env var interpolation.

    Args:
        path: Config path.

    Returns:
        AppConfig: Parsed config model.
    """
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AppConfig(**_render_env_recursive(payload))


def _is_env_placeholder(value: str) -> bool:
    """Whether a string is a whole-value ``${NAME}`` placeholder.

    Args:
        value: String to test.

    Returns:
        bool: True when the string wraps an environment variable name.
    """
    return value.startswith("${") and value.endswith("}")


def _render_env_recursive(obj: Any) -> Any:
    """Recursively interpolate ${ENV} placeholders in nested structures.

    Args:
        obj: Parsed YAML value (dict, list, str, or scalar).

    Returns:
        Any: Value with env vars resolved.
    """
    if isinstance(obj, str):
        return os.environ.get(obj[2:-1], "") if _is_env_placeholder(obj) else obj
    if isinstance(obj, dict):
        return {key: _render_env_recursive(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_render_env_recursive(item) for item in obj]
    return obj
