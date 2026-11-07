from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from trader.data.features import FeatureSpec
from trader.model.tabular import TabularModel
from trader.research.dataset import build_dataset, market_features
from trader.research.labels import LabelSpec
from trader.research.monte_carlo import (
    EnsembleSettings,
    MemberPool,
    SimulationSettings,
    Vote,
    VotingEnsemble,
    block_bootstrap_rows,
    expected_log_return,
    plan_from_paths,
    simulate_paths,
    standardized_shocks,
)
from trader.research.plan_backtest import (
    ExecutionSection,
    PlanSpec,
    _execute_symbol,
    latest_plans,
    main,
    run_backtest,
)
from trader.research.synthetic import synthetic_bars


@pytest.mark.parametrize("estimator", ["random_forest", "extra_trees", "mlp", "knn", "elastic_net"])
@pytest.mark.parametrize("task", ["regression", "classification"])
def test_new_estimators_learn_a_linear_signal(estimator: str, task: str) -> None:
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(400, 3)), columns=["a", "b", "c"])
    signal = X["a"] - 0.5 * X["b"]
    y = signal if task == "regression" else pd.Series(np.sign(signal), dtype=float)
    params = (
        {"n_estimators": 50, "min_samples_leaf": 5}
        if "tree" in estimator or "forest" in estimator
        else {}
    )
    model = TabularModel(estimator=estimator, task=task, params=params)  # type: ignore[arg-type]
    model.fit(X, y)
    assert np.corrcoef(model.score(X), signal)[0, 1] > 0.7


def test_market_features_are_relative_to_the_universe() -> None:
    bars = {f"S{i}": synthetic_bars(n_bars=120, seed=i) for i in range(3)}
    out = market_features(bars, (5,))
    rel_sum = out["rel_ret_5"].groupby(level="timestamp").sum().dropna()
    np.testing.assert_allclose(rel_sum.to_numpy(), 0.0, atol=1e-12)
    assert "beta_20" in out.columns
    dataset = build_dataset(bars, FeatureSpec(groups=("market",), windows=(5,)), LabelSpec())
    assert list(dataset.X.columns) == ["market_ret_5", "rel_ret_5", "beta_20"]


def test_block_bootstrap_draws_whole_date_blocks() -> None:
    dates = pd.bdate_range("2024-01-01", periods=50)
    index = pd.MultiIndex.from_product([dates, ["A", "B"]], names=["timestamp", "symbol"])
    rows = block_bootstrap_rows(
        index.get_level_values("timestamp"), 0.5, 5, np.random.default_rng(0)
    )
    assert len(rows) == 50  # 25 dates x 2 symbols
    picked = index.get_level_values("timestamp")[rows]
    first_block = pd.DatetimeIndex(picked[:10]).unique()
    assert len(first_block) == 5
    assert (np.diff(dates.get_indexer(first_block)) == 1).all()


def _ensemble(threshold: float = 0.6) -> VotingEnsemble:
    return VotingEnsemble(
        [MemberPool("linear", {"alpha": [1.0]})], EnsembleSettings(vote_threshold=threshold)
    )


def test_vote_needs_the_threshold_share() -> None:
    ensemble = _ensemble()
    votes = ensemble.vote(
        np.array([[1.0, 2.0, 3.0, -1.0], [1.0, -1.0, 2.0, -2.0], [-1.0, -2.0, -3.0, 0.5]])
    )
    assert [v.side for v in votes] == ["BUY", "HOLD", "SELL"]
    assert votes[0].share_up == 0.75
    assert votes[0].consensus == 2.0  # median of the winning voters only
    assert votes[2].consensus == -2.0


def test_voting_ensemble_fits_randomised_members() -> None:
    bars = {"A": synthetic_bars(n_bars=300, seed=1), "B": synthetic_bars(n_bars=300, seed=2)}
    dataset = build_dataset(bars, FeatureSpec(groups=("momentum", "volatility")), LabelSpec())
    pool = [
        MemberPool("linear", {"alpha": [1.0, 10.0]}),
        MemberPool("lightgbm", {"n_estimators": [20]}),
    ]
    ensemble = VotingEnsemble(pool, EnsembleSettings(n_members=6, feature_fraction=0.5)).fit(
        dataset.X, dataset.y
    )
    assert len(ensemble.members) == 6
    assert {len(m.features) for m in ensemble.members} == {round(0.5 * dataset.X.shape[1])}
    assert ensemble.predict(dataset.X.head(4)).shape == (4, 6)
    with pytest.raises(ValueError, match="unknown estimators"):
        VotingEnsemble([MemberPool("oracle")])
    with pytest.raises(ValueError, match="at least one"):
        VotingEnsemble([])
    with pytest.raises(RuntimeError, match="not fitted"):
        _ensemble().predict(dataset.X)


def test_simulated_paths_follow_the_drift() -> None:
    shocks = standardized_shocks({"A": synthetic_bars(n_bars=300)}, 20)
    assert abs(shocks.mean()) < 1e-9
    paths = simulate_paths(100.0, 0.01, 0.05, 10, shocks, 20_000, np.random.default_rng(0))
    assert paths.shape == (20_000, 10)
    assert np.log(paths[:, -1] / 100).mean() == pytest.approx(0.05, abs=0.003)
    with pytest.raises(ValueError, match="at least 30"):
        standardized_shocks({"A": synthetic_bars(n_bars=30)}, 20)


def _vote(side: str) -> Vote:
    return Vote(side, 0.8 if side == "BUY" else 0.2, 0.8 if side == "SELL" else 0.2, 0.0, 0.1)  # type: ignore[arg-type]


def test_buy_and_sell_brackets_are_ordered() -> None:
    rng = np.random.default_rng(1)
    shocks = rng.standard_normal(5000)
    sim = SimulationSettings(cost_bps=0.0, min_expected_value=-1.0)
    up = simulate_paths(100.0, 0.02, 0.05, 5, shocks, 2000, rng)
    buy = plan_from_paths(_vote("BUY"), 100.0, up, 0.05, sim)
    assert buy.side == "BUY"
    assert buy.stop_loss < buy.entry <= 100.0 < buy.take_profit
    assert 0.3 < buy.p_fill < 0.7
    down = simulate_paths(100.0, 0.02, -0.05, 5, shocks, 2000, rng)
    sell = plan_from_paths(_vote("SELL"), 100.0, down, -0.05, sim)
    assert sell.side == "SELL"
    assert sell.take_profit < 100.0 <= sell.entry < sell.stop_loss
    assert sell.expected_value > 0


def test_plan_holds_when_costs_eat_the_edge() -> None:
    rng = np.random.default_rng(2)
    paths = simulate_paths(100.0, 0.02, 0.0, 5, rng.standard_normal(5000), 2000, rng)
    plan = plan_from_paths(_vote("BUY"), 100.0, paths, 0.0, SimulationSettings(cost_bps=200.0))
    assert plan.side == "HOLD"
    assert plan.expected_value < 0
    assert math.isfinite(plan.to_dict()["entry"])


def test_expected_log_return_scales_vol_scaled_predictions() -> None:
    assert expected_log_return(1.0, 0.02, 4, vol_scaled=True) == pytest.approx(0.04)
    assert expected_log_return(0.1, 0.02, 4, vol_scaled=False) == pytest.approx(math.log(1.1))


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)


def _plan(day: pd.Timestamp, side: str, entry: float, tp: float, sl: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "side": side,
                "entry": entry,
                "take_profit": tp,
                "stop_loss": sl,
                "horizon": 3,
                "entry_window": 2,
            }
        ],
        index=[day],
    )


def test_execution_fills_limits_and_exits_on_take_profit() -> None:
    bars = _bars(
        [
            (100, 101, 99, 100),
            (100, 100, 97, 98),
            (98, 106, 98, 105),
            (105, 105, 104, 104),
            (104, 104, 104, 104),
        ]
    )
    trades, daily, tried = _execute_symbol(
        "A", bars, _plan(bars.index[0], "BUY", 98.0, 104.0, 95.0), 0.0, True
    )
    assert tried == 1
    (trade,) = trades
    assert (trade.entry, trade.exit, trade.reason) == (98.0, 104.0, "take_profit")
    assert trade.net_return == pytest.approx(104 / 98 - 1)
    assert (1 + daily).prod() - 1 == pytest.approx(trade.net_return)


def test_execution_stops_out_and_skips_unfilled_and_short_plans() -> None:
    bars = _bars(
        [
            (100, 101, 99, 100),
            (99, 100, 98, 99),
            (99, 99, 94, 95),
            (95, 96, 95, 96),
            (96, 96, 96, 96),
        ]
    )
    trades, _, _ = _execute_symbol(
        "A", bars, _plan(bars.index[0], "BUY", 99.0, 110.0, 96.0), 10.0, True
    )
    assert trades[0].reason == "stop"
    assert trades[0].net_return == pytest.approx(96 / 99 - 1 - 0.002)
    unfilled, _, tried = _execute_symbol(
        "A", bars, _plan(bars.index[0], "BUY", 90.0, 110.0, 85.0), 0.0, True
    )
    assert unfilled == []
    assert tried == 1
    short, _, tried = _execute_symbol(
        "A", bars, _plan(bars.index[0], "SELL", 101.0, 95.0, 103.0), 0.0, True
    )
    assert short == []
    assert tried == 0
    shorts, _, _ = _execute_symbol(
        "A", bars, _plan(bars.index[0], "SELL", 99.5, 95.0, 103.0), 0.0, False
    )
    assert shorts[0].reason == "take_profit"
    assert shorts[0].net_return == pytest.approx(1 - 95 / 99.5)


def _small_spec(tmp_path) -> PlanSpec:
    return PlanSpec.model_validate(
        {
            "data": {
                "source": "synthetic",
                "symbols": ["HK.00001", "HK.00002"],
                "synthetic_bars": 500,
            },
            "features": {
                "groups": ["momentum", "reversal", "candles", "market"],
                "windows": [5, 20],
            },
            "label": {"horizon": 5},
            "ensemble": {
                "n_members": 5,
                "vote_threshold": 0.5,
                "pool": [{"estimator": "linear", "params": {"alpha": [1.0, 10.0]}}],
            },
            "simulation": {"n_paths": 200, "min_expected_value": -1.0},
            "validation": {"n_splits": 2, "min_train": 200},
            "execution": {"cost_bps": 0.0},
            "output_dir": str(tmp_path),
        }
    )


def test_run_backtest_writes_plans_trades_and_calibration(tmp_path) -> None:
    summary = run_backtest(_small_spec(tmp_path), tmp_path / "run")
    assert summary["n_plans"] > 100
    assert summary["n_trades"] > 0
    assert set(summary["exit_reasons"]) <= {"take_profit", "stop", "time"}
    for name in ("plans.csv", "trades.csv", "vote_calibration.csv", "summary.json"):
        assert (tmp_path / "run" / name).exists()
    assert json.loads((tmp_path / "run" / "summary.json").read_text())["n_days"] > 0


def test_latest_plans_cover_every_symbol(tmp_path) -> None:
    plans = latest_plans(_small_spec(tmp_path))
    assert sorted(plans.index.get_level_values("symbol")) == ["HK.00001", "HK.00002"]
    assert (plans["horizon"] == 5).all()


def test_plan_spec_rejects_classification_labels_and_cli_runs(tmp_path, capsys) -> None:
    with pytest.raises(ValueError, match="forward_return"):
        PlanSpec.model_validate({"label": {"kind": "triple_barrier"}}).label_spec()
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(_small_spec(tmp_path).model_dump_json())
    main(["plan", "--spec", str(spec_path), "--synthetic"])
    assert "HK.00002" in capsys.readouterr().out
    assert ExecutionSection().long_only
