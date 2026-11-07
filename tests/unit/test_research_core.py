from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from trader.research.labels import LabelSpec, make_labels
from trader.research.scoring import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    information_coefficients,
    positions_from_scores,
    strategy_returns,
    strategy_stats,
)
from trader.research.synthetic import synthetic_bars
from trader.research.validation import purged_walk_forward


def _bars(
    close: list[float], high: list[float] | None = None, low: list[float] | None = None
) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", periods=len(close))
    return pd.DataFrame({"close": close, "high": high or close, "low": low or close}, index=index)


def test_forward_return_label_without_scaling() -> None:
    bars = _bars([100.0, 110.0, 121.0, 133.1])
    labels = make_labels(bars, LabelSpec(horizon=1, vol_scale=False))
    np.testing.assert_allclose(labels.iloc[:3], [0.1, 0.1, 0.1])
    assert math.isnan(labels.iloc[-1])


def test_vol_scaled_label_is_nan_until_volatility_exists() -> None:
    bars = synthetic_bars(n_bars=60)
    labels = make_labels(bars, LabelSpec(horizon=5, vol_window=20))
    assert labels.iloc[:20].isna().all()
    assert labels.iloc[20:-5].notna().all()
    assert labels.iloc[-5:].isna().all()


def test_triple_barrier_picks_first_touch_and_stop_on_ties() -> None:
    flat = [100.0, 101.0] * 5
    close = [*flat, 100.0, 100.0, 100.0, 100.0]
    high = [*flat, 100.0, 120.0, 100.0, 100.0]
    low = [*flat, 100.0, 100.0, 80.0, 100.0]
    spec = LabelSpec(kind="triple_barrier", horizon=2, vol_window=5, profit_take=1.0, stop_loss=1.0)
    labels = make_labels(_bars(close, high, low), spec)
    assert labels.iloc[10] == 1.0  # high touched first, one bar ahead
    assert labels.iloc[11] == -1.0  # low touched next bar
    assert labels.iloc[7] == 0.0  # small oscillation never reaches a barrier
    both = _bars(close, [*flat, 100.0, 120.0, 100.0, 100.0], [*flat, 100.0, 80.0, 100.0, 100.0])
    assert make_labels(both, spec).iloc[10] == -1.0
    assert spec.task == "classification"
    assert spec.key.startswith("tb|h2")


def test_label_rejects_bad_horizon() -> None:
    with pytest.raises(ValueError, match="horizon"):
        make_labels(_bars([1.0, 2.0]), LabelSpec(horizon=0))


def test_walk_forward_purges_horizon_plus_embargo() -> None:
    dates = pd.bdate_range("2020-01-01", periods=400)
    splits = purged_walk_forward(dates, n_splits=4, horizon=5, embargo=2, min_train=200)
    assert len(splits) == 4
    for split in splits:
        gap = dates.get_loc(split.test_dates[0]) - dates.get_loc(split.train_dates[-1])
        assert gap == 8  # 7 purged dates in between
        assert split.train_dates[0] == dates[0]
    assert splits[-1].test_dates[-1] == dates[-1]
    rolling = purged_walk_forward(dates, n_splits=2, horizon=1, min_train=100, expanding=False)
    assert len(rolling[1].train_dates) == 100


def test_walk_forward_needs_enough_dates() -> None:
    with pytest.raises(ValueError, match="need more"):
        purged_walk_forward(pd.bdate_range("2020-01-01", periods=10), n_splits=5, min_train=8)


def test_information_coefficients() -> None:
    x = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert information_coefficients(x, x * 2) == pytest.approx((1.0, 1.0))
    assert information_coefficients(x, pd.Series([1.0, 1.0, 1.0, 1.0])) == (0.0, 0.0)


def test_strategy_returns_charge_costs_on_position_changes() -> None:
    index = pd.MultiIndex.from_product(
        [pd.bdate_range("2024-01-01", periods=3), ["A"]], names=["timestamp", "symbol"]
    )
    scores = pd.Series([1.0, -1.0, 0.5], index=index)
    assert positions_from_scores(scores, long_only=False).tolist() == [1.0, -1.0, 1.0]
    positions = positions_from_scores(scores)
    assert positions.tolist() == [1.0, 0.0, 1.0]
    returns, turnover = strategy_returns(positions, pd.Series(0.01, index=index), cost_bps=10)
    np.testing.assert_allclose(returns.to_numpy(), [0.009, -0.001, 0.009])
    assert turnover.tolist() == [1.0, 1.0, 1.0]


def test_strategy_stats_refuses_sharpe_on_short_samples() -> None:
    short = strategy_stats(pd.Series([0.01, -0.01]), pd.Series([1.0, 1.0]))
    assert math.isnan(short.sharpe)
    rng = np.random.default_rng(0)
    long = strategy_stats(pd.Series(rng.normal(0.001, 0.01, 500)), pd.Series(np.ones(500)))
    assert long.n_obs == 500
    assert long.sharpe_annual == pytest.approx(long.sharpe * math.sqrt(252))
    assert 0 <= long.max_drawdown < 1


def test_deflated_sharpe_falls_as_trials_grow() -> None:
    assert expected_max_sharpe(0.01, 1) == 0.0
    few = deflated_sharpe_ratio(0.1, 500, 0.0, 3.0, 0.002, 2)
    many = deflated_sharpe_ratio(0.1, 500, 0.0, 3.0, 0.002, 1000)
    assert 0 <= many < few <= 1
    assert math.isnan(deflated_sharpe_ratio(math.nan, 500, 0.0, 3.0, 0.002, 10))
