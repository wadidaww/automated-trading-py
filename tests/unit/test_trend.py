"""Trend features, the trend model and its wiring (config, registry, daily job)."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from trader.adapters.futu.client import FutuClient
from trader.adapters.sim.simulator import PaperSimulator
from trader.app.bootstrap import DefaultPipelineFactory
from trader.config import AppConfig, load_config
from trader.data import indicators
from trader.data.features import (
    LIVE_FEATURES,
    TREND_FEATURES,
    TrendSpec,
    live_feature_frame,
    trend_features,
)
from trader.jobs import daily_signals as ds
from trader.jobs.daily_signals import DailySignalJob
from trader.marketdata.quote_handler import QuoteEvent
from trader.model.base import Signal
from trader.model.registry import build_model
from trader.model.trend import TrendFollowingModel
from trader.pipeline.data_stage import DataStage
from trader.pipeline.signal_stage import SignalStage
from trader.research.synthetic import synthetic_bars

SPEC = TrendSpec(
    lookbacks=(10, 20, 40), donchian_entry=8, donchian_exit=4, vol_window=5, sma_window=30
)
TREND_CONFIG = "config/config.crypto.trend.yaml"
STAMP = datetime(2026, 11, 6, tzinfo=UTC)


def _closes(n: int = 300, seed: int = 7) -> pd.Series:
    return synthetic_bars(n_bars=n, seed=seed)["close"].astype(float)


def _reference(close: pd.Series, spec: TrendSpec) -> pd.DataFrame:
    """Vectorised full-history versions of the trend features (the research backtest's form)."""
    blend = sum(close / close.shift(lb) - 1 for lb in spec.lookbacks) / len(spec.lookbacks)
    high = close.rolling(spec.donchian_entry).max().shift(1)
    low = close.rolling(spec.donchian_exit).min().shift(1)
    state, long = [], False
    for price, hi, lo in zip(close, high, low, strict=True):
        if not long and price > hi:
            long = True
        elif long and price < lo:
            long = False
        state.append(1.0 if long else 0.0)
    vol = close.pct_change().rolling(spec.vol_window).std(ddof=1) * math.sqrt(spec.bars_per_year)
    return pd.DataFrame(
        {"trend_return": blend, "donchian_long": state, "realised_vol": vol}, index=close.index
    )


def test_trend_features_match_the_vectorised_reference() -> None:
    close = _closes()
    expected = _reference(close, SPEC)
    values = close.tolist()
    for end in range(SPEC.min_points + 60, len(values)):
        got = trend_features(values[: end + 1], SPEC)
        row = expected.iloc[end]
        assert got["trend_return"] == pytest.approx(row["trend_return"], rel=1e-9)
        assert got["realised_vol"] == pytest.approx(row["realised_vol"], rel=1e-9)
        assert got["donchian_long"] == row["donchian_long"]


def test_trend_features_are_nan_until_the_window_is_long_enough() -> None:
    values = _closes().tolist()
    short = trend_features(values[: SPEC.min_points - 1], SPEC)
    assert math.isnan(short["trend_return"])
    assert all(math.isfinite(v) for v in trend_features(values[: SPEC.min_points], SPEC).values())
    assert math.isnan(indicators.donchian_long(values[:8], 8, 4))
    assert math.isnan(indicators.realised_vol(values[:5], 5, 365))
    assert math.isnan(indicators.blended_return([0.0, 1.0, 2.0], [2]))  # non-positive base
    assert math.isnan(indicators.realised_vol([1.0, 0.0, 1.0], 2, 365))


async def test_data_stage_trend_features_match_live_feature_frame() -> None:
    close = _closes(120)
    stage = DataStage(window_size=60, trend=SPEC)
    live = []
    for price in close:
        window = await stage.process(
            QuoteEvent(symbol="CC.BTCUSD", price=float(price), timestamp=STAMP)
        )
        live.append([getattr(window, name) for name in TREND_FEATURES])
    offline = live_feature_frame(close, window=60, trend=SPEC)
    assert list(offline.columns) == [*LIVE_FEATURES, *TREND_FEATURES]
    np.testing.assert_allclose(
        np.array(live), offline[list(TREND_FEATURES)].to_numpy(), rtol=1e-12, equal_nan=True
    )
    assert np.isnan(np.array(live)[: SPEC.min_points - 1, 0]).all()
    assert np.isfinite(np.array(live)[-1]).all()


async def test_data_stage_without_a_spec_leaves_trend_features_nan() -> None:
    stage = DataStage(window_size=60)
    window = None
    for price in _closes(80):
        window = await stage.process(
            QuoteEvent(symbol="HK.00700", price=float(price), timestamp=STAMP)
        )
    assert window is not None
    assert math.isnan(window.trend_return)
    assert math.isnan(window.realised_vol)


def _row(trend_return: float, donchian: float) -> pd.DataFrame:
    return pd.DataFrame({"trend_return": [trend_return], "donchian_long": [donchian]})


@pytest.mark.parametrize(
    ("trend_return", "donchian", "signal", "exposure"),
    [
        (0.2, 1.0, Signal.BUY, 1.0),
        (0.2, 0.0, Signal.BUY, 0.5),
        (-0.2, 1.0, Signal.BUY, 0.5),
        (-0.2, 0.0, Signal.SELL, 0.0),
        (0.0, 0.0, Signal.SELL, 0.0),
    ],
)
def test_model_votes(trend_return: float, donchian: float, signal: Signal, exposure: float) -> None:
    prediction = TrendFollowingModel().predict(_row(trend_return, donchian))
    assert prediction.signal is signal
    assert prediction.target_exposure == exposure
    assert prediction.confidence == 1.0


@pytest.mark.parametrize(
    "features",
    [_row(math.nan, 1.0), _row(0.2, math.nan), pd.DataFrame({"z_score": [1.0]}), pd.DataFrame()],
)
def test_model_holds_when_a_trend_feature_is_missing(features: pd.DataFrame) -> None:
    prediction = TrendFollowingModel().predict(features)
    assert prediction.signal is Signal.HOLD
    assert prediction.target_exposure is None
    assert prediction.confidence == 0.0


def test_model_decision_ignores_future_bars() -> None:
    close = _closes()
    cut = 200
    shocked = close.copy()
    shocked.iloc[cut + 1 :] *= 3.0
    model = TrendFollowingModel()
    base = live_feature_frame(close, window=120, trend=SPEC).iloc[: cut + 1]
    after = live_feature_frame(shocked, window=120, trend=SPEC).iloc[: cut + 1]
    pd.testing.assert_frame_equal(base, after)
    assert model.predict(base).target_exposure == model.predict(after).target_exposure


def test_model_is_registered_and_round_trips(tmp_path: Path) -> None:
    model = build_model("trend")
    assert isinstance(model, TrendFollowingModel)
    model.fit(pd.DataFrame(), pd.Series(dtype=float))
    path = tmp_path / "trend.json"
    model.save(str(path))
    assert isinstance(TrendFollowingModel.load(str(path)), TrendFollowingModel)
    path.write_text('{"model": "other"}')
    with pytest.raises(ValueError, match="not a trend model"):
        TrendFollowingModel.load(str(path))


async def test_signal_stage_carries_target_and_volatility() -> None:
    stage = SignalStage(
        TrendFollowingModel(),
        confidence_threshold=0.6,
        cooldown_seconds=0,
        feature_columns=(*LIVE_FEATURES, *TREND_FEATURES),
    )
    data = DataStage(window_size=120, trend=SPEC)
    window = None
    for price in np.linspace(100.0, 200.0, 120):
        window = await data.process(
            QuoteEvent(symbol="CC.BTCUSD", price=float(price), timestamp=STAMP)
        )
    assert window is not None
    signal = await stage.process(window)
    assert signal is not None
    assert signal.signal is Signal.BUY
    assert signal.target_exposure == 1.0
    assert signal.realised_vol == pytest.approx(window.realised_vol)


def test_donchian_over_a_long_window_matches_the_full_history() -> None:
    spec = TrendSpec()
    for seed in range(5):
        values = _closes(900, seed=seed).tolist()
        for end in range(420, len(values), 7):
            windowed = indicators.donchian_long(
                values[end - 399 : end + 1], spec.donchian_entry, spec.donchian_exit
            )
            full = indicators.donchian_long(
                values[: end + 1], spec.donchian_entry, spec.donchian_exit
            )
            assert windowed == full


async def test_models_fitted_on_live_features_never_see_trend_columns() -> None:
    from trader.model.gradient_boosting import GradientBoostingModel

    frame = live_feature_frame(_closes(200), window=60).dropna()
    model = GradientBoostingModel(random_state=1)
    model.fit(frame, pd.Series(np.arange(len(frame)) % 2, index=frame.index))
    data = DataStage(window_size=60)
    window = None
    for price in _closes(80):
        window = await data.process(
            QuoteEvent(symbol="HK.00700", price=float(price), timestamp=STAMP)
        )
    assert window is not None
    stage = SignalStage(model, confidence_threshold=0.0, cooldown_seconds=0)
    assert await stage.process(window) is not None


def test_config_and_feature_spec_agree_on_the_minimum_window() -> None:
    settings = load_config(TREND_CONFIG).model.trend
    assert settings.min_points == TrendSpec(lookbacks=tuple(settings.lookbacks)).min_points
    assert TrendSpec().lookbacks == tuple(settings.lookbacks)


def test_tick_pipeline_refuses_the_trend_model() -> None:
    import asyncio

    from trader.app import runtime

    with pytest.raises(SystemExit, match="--daily-signals"):
        asyncio.run(runtime.run("paper", 1, TREND_CONFIG))


# --- config ---------------------------------------------------------------------------------


def _trend_config(**changes: Any) -> dict[str, Any]:
    payload = load_config(TREND_CONFIG).model_dump()
    for dotted, value in changes.items():
        node = payload
        *parents, leaf = dotted.split("__")
        for key in parents:
            node = node[key]
        node[leaf] = value
    return payload


def test_trend_config_loads_and_is_paper_only() -> None:
    config = load_config(TREND_CONFIG)
    assert config.model.type == "trend"
    assert config.trading.trd_env == "SIMULATE"
    assert config.risk.vol_target.enabled
    assert config.pipeline.data_window_size >= config.model.trend.min_points
    assert config.risk.vol_target.symbol_weight <= config.trading.concentration_limit_pct


def test_vol_target_is_off_by_default() -> None:
    for name in ("dev", "staging", "prod", "crypto.dev"):
        assert not load_config(f"config/config.{name}.yaml").risk.vol_target.enabled


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"risk__vol_target__enabled": False}, "requires risk.vol_target.enabled"),
        ({"risk__vol_target__symbol_weight": 0.5}, "concentration_limit_pct"),
        ({"pipeline__data_window_size": 365}, "data_window_size >= 366"),
        (
            {"model__trend__lookbacks": [20, 40], "pipeline__data_window_size": 60},
            "data_window_size >= 110",
        ),
        ({"pipeline__feature_interval": "5m"}, "feature_interval"),
        ({"daily__history_days": 300}, "history_days must be >= 400"),
        ({"model__trend__lookbacks": [0, 30]}, "lookbacks must be positive"),
        ({"risk__vol_target__target_vol_annual": 0.0}, "greater than 0"),
        ({"risk__vol_target__symbol_weight": 1.5}, "less than or equal to 1"),
    ],
)
def test_trend_config_that_cannot_work_fails_at_load(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        AppConfig(**_trend_config(**changes))


def test_factory_wires_trend_features_and_sizing() -> None:
    factory = DefaultPipelineFactory(config=load_config(TREND_CONFIG), client=FutuClient())
    assert factory.create_data_stage()._trend == TrendSpec()
    assert isinstance(factory.create_signal_stage().model, TrendFollowingModel)
    assert factory.create_risk_stage().risk._vol_target is not None
    plain = DefaultPipelineFactory(
        config=load_config("config/config.crypto.dev.yaml"), client=FutuClient()
    )
    assert factory.create_signal_stage()._feature_columns == (*LIVE_FEATURES, *TREND_FEATURES)
    assert plain.create_signal_stage()._feature_columns == LIVE_FEATURES
    assert plain.create_data_stage()._trend is None
    assert plain.create_risk_stage().risk._vol_target is None


# --- daily job, end to end in dry-run -------------------------------------------------------

TODAY = date(2026, 11, 6)
NOW_NS = int(datetime(2026, 11, 6, 2, 0, tzinfo=UTC).timestamp() * 1e9)


class _Clock:
    def now_ns(self) -> int:
        return NOW_NS

    def monotonic_ns(self) -> int:
        return NOW_NS


class _EveryDay:
    def trading_days(self, symbol: str, start: date, end: date) -> list[date]:
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]


class _Bars:
    def __init__(self, closes: np.ndarray) -> None:
        days = pd.date_range(end=TODAY - timedelta(days=1), periods=len(closes))
        self.frame = pd.DataFrame({"close": closes}, index=days)

    def fetch(self, symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        return self.frame


class _Pinned(PaperSimulator):
    def __init__(self, price: float) -> None:
        super().__init__(lot_size=None)
        self._pinned = price

    def advance_price(self, symbol: str) -> float:
        return self._pinned


async def _daily(tmp_path: Path, closes: np.ndarray) -> ds.DailyDecision:
    base = load_config(TREND_CONFIG)
    config = base.model_copy(
        update={
            "trading": base.trading.model_copy(update={"symbols": ["CC.BTCUSD"]}),
            "daily": base.daily.model_copy(update={"state_file": str(tmp_path / "state.json")}),
            "risk": base.risk.model_copy(update={"kill_switch_file": str(tmp_path / "KILL")}),
        }
    )
    from trader.adapters.factory import create_client

    client = create_client(config, "paper")
    client._simulator = _Pinned(float(closes[-1]))  # type: ignore[attr-defined]
    job = DailySignalJob(
        config,
        DefaultPipelineFactory(config=config, client=client),
        bars=_Bars(closes),
        calendar=_EveryDay(),
        dry_run=True,
        clock=_Clock(),
    )
    [decision] = await job.run()
    return decision


async def test_daily_job_uptrend_buys_towards_the_target(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    closes = 2000.0 * np.cumprod(1 + 0.002 + 0.01 * rng.standard_normal(420))
    decision = await _daily(tmp_path, closes)
    assert decision.action == ds.DRY_RUN, decision
    assert decision.side == "BUY"
    assert decision.qty is not None
    assert decision.limit_price is not None
    notional = decision.qty * decision.limit_price
    # 4% of the 100,000 paper account, capped by the 2,000 per-order limit.
    assert 0 < notional <= 2000.0


async def test_daily_job_downtrend_without_position_places_nothing(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    closes = 2000.0 * np.cumprod(1 - 0.002 + 0.01 * rng.standard_normal(420))
    decision = await _daily(tmp_path, closes)
    assert decision.action == ds.REJECTED, decision
    assert decision.detail == "at_target"


async def test_daily_job_short_history_is_refused(tmp_path: Path) -> None:
    decision = await _daily(tmp_path, np.linspace(1000.0, 2000.0, 200))
    assert decision.action == ds.INSUFFICIENT_HISTORY
