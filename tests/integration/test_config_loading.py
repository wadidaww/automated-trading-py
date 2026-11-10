from __future__ import annotations

from trader.config import load_config


def test_config_loading() -> None:
    cfg = load_config("config/config.dev.yaml")
    assert cfg.opend.port == 11111


def test_trend_and_vol_target_settings_load() -> None:
    dev = load_config("config/config.dev.yaml")
    assert not dev.risk.vol_target.enabled
    assert dev.model.trend.lookbacks == [91, 182, 365]
    trend = load_config("config/config.crypto.trend.yaml")
    assert trend.model.type == "trend"
    assert trend.risk.vol_target.enabled
    assert trend.trading.trd_env == "SIMULATE"
    assert trend.trading.symbols == ["CC.BTCUSD", "CC.ETHUSD"]
