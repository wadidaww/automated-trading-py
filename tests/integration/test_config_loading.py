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


def test_sma_trend_configs_load(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FUTU_ACCOUNT_ID", "0")
    monkeypatch.setenv("FUTU_SECURITY_FIRM", "FUTUSECURITIES")
    assert load_config("config/config.dev.yaml").model.trend.sma_window == 200
    crypto = load_config("config/config.crypto.sma.yaml")
    hk = load_config("config/config.hk.nasdaq.sma.yaml")
    assert (crypto.trading.symbols, hk.trading.symbols) == (["CC.BTCUSD"], ["HK.02834"])
    for config in (crypto, hk):
        assert config.model.type == "sma_trend"
        assert config.trading.trd_env == "SIMULATE"
        assert config.risk.vol_target.enabled
        assert config.pipeline.data_window_size >= config.model.trend.sma_min_points
    staging = load_config("config/config.staging.yaml")
    assert hk.trading.max_position_notional == staging.trading.max_position_notional
    assert hk.risk.max_order_notional == staging.risk.max_order_notional
    # The whole target fits inside the per-symbol cap on a HKD 1,000,000 paper account.
    assert hk.risk.vol_target.symbol_weight * 1_000_000 <= hk.trading.max_position_notional
