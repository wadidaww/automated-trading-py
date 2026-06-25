"""Configuration loading helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpendConfig(BaseModel):
    """OpenD gateway config."""

    host: str
    port: int
    heartbeat_interval_s: int = 10
    reconnect_max_attempts: int = 5
    rate_limit_requests: int = 300
    rate_limit_window_s: int = 30


class TradingSettings(BaseModel):
    """Trading runtime settings."""

    account_id: str = Field(...)
    market: str = "HK"
    symbols: list[str]
    max_position_notional_hkd: int
    max_portfolio_notional_hkd: int
    max_daily_loss_hkd: int
    max_open_orders: int
    concentration_limit_pct: float
    signal_cooldown_s: int
    order_type: str = "LIMIT"


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
    model_config = SettingsConfigDict(env_file=".env")


def load_config(path: str) -> AppConfig:
    """Load YAML config file.

    Args:
        path: Config path.

    Returns:
        AppConfig: Parsed config model.
    """
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AppConfig(**payload)


def render_env(value: str) -> str:
    """Render ${ENV} syntax from current process env if present.

    Args:
        value: Raw text.

    Returns:
        str: Interpolated text.
    """
    if value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
        import os

        return os.environ.get(env_name, "")
    return value


def flatten_dict(source: dict[str, Any]) -> dict[str, Any]:
    """Return same dict; helper kept for extension.

    Args:
        source: Input mapping.

    Returns:
        dict[str, Any]: Copied mapping.
    """
    return dict(source)
