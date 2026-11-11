"""The moving-average trend model, its feature and its wiring."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from trader.app import runtime
from trader.app.bootstrap import DefaultPipelineFactory
from trader.config import AppConfig, load_config
from trader.data import indicators
from trader.data.features import TREND_FEATURES, TrendSpec, live_feature_frame, trend_features
from trader.model.base import Signal
from trader.model.registry import build_model
from trader.model.sma_trend import SmaTrendModel
from trader.research.synthetic import synthetic_bars

SPEC = TrendSpec(lookbacks=(10,), donchian_entry=8, donchian_exit=4, vol_window=5, sma_window=20)
CONFIGS = ("config/config.crypto.sma.yaml", "config/config.hk.nasdaq.sma.yaml")


def _closes(n: int = 200, seed: int = 11) -> pd.Series:
    return synthetic_bars(n_bars=n, seed=seed)["close"].astype(float)


def _row(gap: float) -> pd.DataFrame:
    return pd.DataFrame([{"sma_gap": gap}])


def test_sma_gap_matches_the_rolling_mean_reference() -> None:
    close = _closes()
    expected = close / close.rolling(SPEC.sma_window).mean() - 1
    values = close.tolist()
    for end in range(len(values)):
        got = indicators.sma_gap(values[: end + 1], SPEC.sma_window)
        if end + 1 < SPEC.sma_window:
            assert math.isnan(got)
        else:
            assert got == pytest.approx(expected.iloc[end], rel=1e-9, abs=1e-12)


def test_sma_gap_rejects_bad_input() -> None:
    assert math.isnan(indicators.sma_gap([1.0, 2.0], 0))
    assert math.isnan(indicators.sma_gap([0.0, 0.0], 2))
    assert math.isnan(indicators.sma_gap([0.0, 100.0, 100.0], 3))  # a zero print is not a rally
    assert math.isnan(indicators.sma_gap([100.0, math.inf, 100.0], 3))  # nor is inf an exit
    assert math.isnan(indicators.sma_gap([100.0, -5.0, 100.0], 3))
    assert math.isnan(indicators.sma_gap([100.0, math.nan, 100.0], 3))
    assert "sma_gap" in TREND_FEATURES
    assert "sma_gap" in trend_features(_closes(50).tolist(), SPEC)


def test_model_holds_above_the_average_and_exits_below() -> None:
    model = SmaTrendModel()
    above = model.predict(_row(0.03))
    assert (above.signal, above.target_exposure, above.confidence) == (Signal.BUY, 1.0, 1.0)
    below = model.predict(_row(-0.01))
    assert (below.signal, below.target_exposure) == (Signal.SELL, 0.0)
    assert model.predict(_row(0.0)).signal is Signal.SELL  # at the average is not above it


@pytest.mark.parametrize("frame", [_row(math.nan), pd.DataFrame(), pd.DataFrame([{"rsi": 50.0}])])
def test_model_holds_when_the_feature_is_missing(frame: pd.DataFrame) -> None:
    prediction = SmaTrendModel().predict(frame)
    assert prediction.signal is Signal.HOLD
    assert prediction.target_exposure is None


def test_decisions_do_not_look_ahead() -> None:
    close = _closes()
    cut = 150
    shocked = close.copy()
    shocked.iloc[cut + 1 :] *= 3.0
    model = SmaTrendModel()
    base = live_feature_frame(close, window=60, trend=SPEC)
    after = live_feature_frame(shocked, window=60, trend=SPEC)
    for i in range(cut + 1):
        assert model.predict(base.iloc[[i]]).signal is model.predict(after.iloc[[i]]).signal


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sma.json"
    SmaTrendModel().save(str(path))
    assert isinstance(SmaTrendModel.load(str(path)), SmaTrendModel)
    path.write_text('{"model": "trend"}')
    with pytest.raises(ValueError, match="not an sma_trend"):
        SmaTrendModel.load(str(path))
    SmaTrendModel().fit(pd.DataFrame(), pd.Series(dtype=float))


@pytest.mark.parametrize("path", CONFIGS)
def test_paper_configs_load_and_stay_simulated(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUTU_ACCOUNT_ID", "0")
    monkeypatch.setenv("FUTU_SECURITY_FIRM", "FUTUSECURITIES")
    config = load_config(path)
    assert config.model.type == "sma_trend"
    assert config.trading.trd_env == "SIMULATE"
    assert config.risk.vol_target.enabled
    assert config.risk.vol_target.symbol_weight <= config.trading.concentration_limit_pct
    assert config.pipeline.data_window_size >= config.model.trend.sma_min_points
    assert isinstance(build_model(config.model.type, config.model), SmaTrendModel)
    factory = DefaultPipelineFactory(config)
    assert factory.create_data_stage() is not None
    assert factory.create_signal_stage() is not None


def _payload(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("FUTU_ACCOUNT_ID", "0")
    monkeypatch.setenv("FUTU_SECURITY_FIRM", "FUTUSECURITIES")
    return load_config(CONFIGS[0]).model_dump()


def test_config_needs_vol_target_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload(monkeypatch)
    payload["risk"]["vol_target"]["enabled"] = False
    with pytest.raises(ValidationError, match="sma_trend.*vol_target"):
        AppConfig(**payload)


def test_config_needs_a_window_that_covers_the_average(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload(monkeypatch)
    payload["model"]["trend"]["sma_window"] = 500
    with pytest.raises(ValidationError, match="data_window_size >= 500"):
        AppConfig(**payload)


async def test_tick_pipeline_refuses_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUTU_ACCOUNT_ID", "0")
    monkeypatch.setenv("FUTU_SECURITY_FIRM", "FUTUSECURITIES")
    with pytest.raises(SystemExit, match="daily-signals"):
        await runtime.run("paper", 1, CONFIGS[0])
