"""Configuration loading helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from datetime import date
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trader.core.symbols import normalize_symbol

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


class TradingSettings(BaseModel):
    """Trading runtime settings."""

    account_id: str = Field(...)
    market: str = "HK"
    # SIMULATE or REAL. REAL additionally requires --mode live and TRADER_LIVE_CONFIRM=1.
    trd_env: Literal["SIMULATE", "REAL"] = "SIMULATE"
    symbols: list[str]
    max_position_notional_hkd: int
    max_portfolio_notional_hkd: int
    max_daily_loss_hkd: int
    max_open_orders: int
    concentration_limit_pct: float
    signal_cooldown_s: int
    order_type: str = "LIMIT"
    strategy_name: str = "default"

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: list[str]) -> list[str]:
        """Normalise every code to Futu ``MARKET.CODE`` form (``700.HK`` → ``HK.00700``)."""
        return [normalize_symbol(symbol) for symbol in value]


class RiskSettings(BaseModel):
    """Pre-trade and loss limits. Money values are in account currency major units."""

    max_order_qty: int = 10_000
    max_order_notional: float = 50_000.0
    max_position_notional: float = 100_000.0
    max_gross_notional: float = 500_000.0
    max_daily_loss: float = 10_000.0
    max_open_orders: int = 10
    concentration_limit_pct: float = 0.25
    max_orders_per_second: int = 2
    max_orders_per_30s: int = 12
    price_band_pct: float = 0.02
    max_quote_age_ms: int = 3_000
    allow_short: bool = False
    kill_switch_file: str = "run/KILL"
    cancel_on_exit: bool = True
    # Kelly fraction multiplier applied on top of the model's edge estimate (fractional Kelly).
    kelly_fraction: float = 0.25

    @model_validator(mode="after")
    def _check_limits(self) -> RiskSettings:
        """Reject configs that would exceed the broker's documented order-rate limit."""
        if self.max_orders_per_30s >= FUTU_MAX_ORDERS_PER_30S:
            raise ValueError(f"max_orders_per_30s must be < {FUTU_MAX_ORDERS_PER_30S}")
        if not 0.0 < self.price_band_pct < 0.2:
            raise ValueError("price_band_pct must be in (0, 0.2)")
        if not 0.0 < self.kelly_fraction <= 1.0:
            raise ValueError("kelly_fraction must be in (0, 1]")
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


class ModelSettings(BaseModel):
    """Model config."""

    type: str
    path: str
    hot_reload: bool
    confidence_threshold: float


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
    model_config = SettingsConfigDict(env_file=".env")


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
