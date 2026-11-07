from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trader.api.client import FutuClient
from trader.data.features import LIVE_FEATURES, FeatureSpec
from trader.data.fetcher import cache_path
from trader.model.base import Prediction, Signal
from trader.model.ensemble import EnsembleSignalModel
from trader.model.mean_reversion import MeanReversionModel
from trader.model.tabular import TabularModel
from trader.model.trainer import _cli as train_cli
from trader.pipeline.factory import DefaultPipelineFactory
from trader.research.dataset import build_dataset
from trader.research.grid_search import GridSearch, GridSpec, build_trials, expand, main
from trader.research.labels import LabelSpec
from trader.research.synthetic import synthetic_bars
from trader.utils.config import load_config


def _xy(task: str) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(300, 3)), columns=["a", "b", "c"])
    signal = X["a"] - 0.5 * X["b"]
    if task == "regression":
        return X, signal + rng.normal(scale=0.1, size=300)
    return X, pd.Series(np.sign(signal.round(1)), dtype=float)


@pytest.mark.parametrize("estimator", ["lightgbm", "linear", "sklearn_gbm"])
@pytest.mark.parametrize("task", ["regression", "classification"])
def test_tabular_model_learns_and_round_trips(estimator: str, task: str, tmp_path) -> None:
    X, y = _xy(task)
    params = {"n_estimators": 50} if estimator == "lightgbm" else {}
    model = TabularModel(estimator=estimator, task=task, params=params)  # type: ignore[arg-type]
    model.fit(X, y)
    scores = model.score(X)
    assert np.corrcoef(scores, X["a"] - 0.5 * X["b"])[0, 1] > 0.8
    up = model.predict(pd.DataFrame({"a": [3.0], "b": [-3.0], "c": [0.0]}))
    assert up.signal == Signal.BUY
    assert 0.0 < up.confidence <= 1.0
    path = tmp_path / "m.joblib"
    model.save(str(path))
    loaded = TabularModel.load(str(path))
    np.testing.assert_allclose(loaded.score(X), scores)


def test_tabular_model_guards() -> None:
    with pytest.raises(ValueError, match="unknown estimator"):
        TabularModel(estimator="oracle")
    model = TabularModel(estimator="linear", min_score=10.0)
    with pytest.raises(RuntimeError, match="not fitted"):
        model.predict(pd.DataFrame({"a": [1.0]}))
    with pytest.raises(RuntimeError, match="unfitted"):
        model.save("unused")
    with pytest.raises(ValueError, match="matching"):
        model.fit(pd.DataFrame(), pd.Series(dtype=float))
    X, y = _xy("regression")
    model.fit(X, y)
    assert model.predict(X.head(1)).signal == Signal.HOLD


def test_ensemble_holds_when_every_vote_has_zero_confidence() -> None:
    class Zero(MeanReversionModel):
        def predict(self, features: pd.DataFrame) -> Prediction:
            return Prediction(signal=Signal.SELL, confidence=0.0, metadata={})

    ensemble = EnsembleSignalModel()
    ensemble.add_model(Zero(), weight=1.0, name="zero")
    prediction = ensemble.predict(pd.DataFrame({"z_score": [0.0]}))
    assert prediction.signal == Signal.HOLD
    assert prediction.confidence == 0.0


def test_expand_and_build_trials() -> None:
    assert expand({"a": [1, 2], "b": 3}) == [{"a": 1, "b": 3}, {"a": 2, "b": 3}]
    assert expand({"windows": [5, 20]}, frozenset({"windows"})) == [{"windows": [5, 20]}]
    assert len(expand({"windows": [[5], [20]]}, frozenset({"windows"}))) == 2
    spec = GridSpec.model_validate(
        {
            "features": [{"groups": ["momentum"], "windows": [[5, 20], [10, 60]]}],
            "labels": [{"horizon": [5, 10]}],
            "models": [
                {"estimator": "linear", "params": {"alpha": [1.0, 10.0]}},
                {"estimator": "lightgbm"},
            ],
        }
    )
    trials = build_trials(spec)
    assert len(trials) == 2 * 2 * 3
    assert trials[0].features.windows == (5, 20)
    with pytest.raises(ValueError, match="unknown estimator"):
        build_trials(GridSpec.model_validate({"models": [{"estimator": "oracle"}]}))


def _small_spec(tmp_path: Path) -> GridSpec:
    return GridSpec.model_validate(
        {
            "data": {
                "source": "synthetic",
                "symbols": ["HK.00001", "HK.00002"],
                "synthetic_bars": 450,
                "synthetic_autocorrelation": 0.3,
            },
            "features": [
                {"groups": ["reversal", "volatility"], "windows": [5, 20]},
                {"groups": ["live"], "live_window": 30},
            ],
            "labels": [
                {"kind": "forward_return", "horizon": 1},
                {"kind": "triple_barrier", "horizon": 5},
            ],
            "models": [
                {"estimator": "linear", "params": {"alpha": [1.0]}},
                {"estimator": "lightgbm", "params": {"n_estimators": [30], "num_leaves": [7]}},
            ],
            "validation": {"n_splits": 3, "embargo": 2, "min_train": 150},
            "output": {"dir": str(tmp_path), "select_by": "rank_ic"},
        }
    )


def test_grid_search_runs_logs_and_refits(tmp_path) -> None:
    run_dir = tmp_path / "run"
    summary = GridSearch(_small_spec(tmp_path), run_dir=run_dir).run()
    assert summary["n_trials"] == 8
    assert summary["n_failed"] == 0
    lines = (run_dir / "trials.jsonl").read_text().splitlines()
    assert len(lines) == 8
    entry = json.loads(lines[0])
    assert {"data_hash", "code_sha", "metrics", "label", "features"} <= set(entry)
    assert 0.0 <= summary["deflated_sharpe"] <= 1.0
    # The synthetic series has planted short-term momentum, so the winner should find it.
    assert summary["best"]["metrics"]["rank_ic"] > 0
    model = TabularModel.load(summary["best_model"]["path"])
    assert model.feature_names == summary["best_model"]["feature_names"]
    assert len(pd.read_csv(run_dir / "leaderboard.csv")) == 8


def test_grid_search_records_failed_trials(tmp_path) -> None:
    spec = _small_spec(tmp_path)
    spec.validation.min_train = 10_000
    summary = GridSearch(spec, run_dir=tmp_path / "bad").run(max_trials=1)
    assert summary["n_failed"] == 1
    assert summary["best"] is None


def test_grid_search_cli_dry_run(tmp_path, capsys) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text("models:\n  - estimator: linear\n    params: {alpha: [1, 2]}\n")
    main(["--spec", str(spec_path), "--dry-run"])
    assert capsys.readouterr().out.strip().endswith("2 trials")


def test_example_spec_is_valid() -> None:
    from trader.research.grid_search import load_spec

    assert len(build_trials(load_spec("config/research.example.yaml"))) == 66


def _factory(tmp_path: Path, model_type: str, path: Path) -> DefaultPipelineFactory:
    config = load_config("config/config.dev.yaml")
    config.model.type = model_type
    config.model.path = str(path)
    return DefaultPipelineFactory(config, client=FutuClient())


def test_factory_loads_live_compatible_tabular_model(tmp_path) -> None:
    bars = {"HK.00700": synthetic_bars(n_bars=300)}
    dataset = build_dataset(
        bars, FeatureSpec(groups=("live",), live_window=30), LabelSpec(horizon=1)
    )
    model = TabularModel(estimator="linear")
    model.fit(dataset.X, dataset.y)
    path = tmp_path / "live.joblib"
    model.save(str(path))
    stage = _factory(tmp_path, "linear", path).create_signal_stage()
    assert isinstance(stage.model, TabularModel)
    assert set(stage.model.feature_names) == set(LIVE_FEATURES)
    with pytest.raises(ValueError, match="artifact is a 'linear'"):
        _factory(tmp_path, "lightgbm", path).create_signal_stage()


def test_factory_rejects_missing_or_non_live_artifacts(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="fitted artifact"):
        _factory(tmp_path, "lightgbm", tmp_path / "absent.joblib").create_signal_stage()
    with pytest.raises(FileNotFoundError, match="fitted artifact"):
        _factory(tmp_path, "gradient_boosting", tmp_path / "absent.pkl").create_signal_stage()
    X, y = _xy("regression")
    model = TabularModel(estimator="linear")
    model.fit(X, y)
    path = tmp_path / "research.joblib"
    model.save(str(path))
    with pytest.raises(ValueError, match="live pipeline does not compute"):
        _factory(tmp_path, "tabular", path).create_signal_stage()
    with pytest.raises(ValueError, match="needs a config"):
        DefaultPipelineFactory(client=FutuClient())._create_tabular_model("linear")


def test_trainer_cli_trains_from_cache(tmp_path, capsys) -> None:
    path = cache_path("HK.00700", cache_dir=str(tmp_path))
    synthetic_bars(n_bars=200).to_csv(path, index_label="timestamp")
    out = tmp_path / "models" / "model.pkl"
    train_cli(
        [
            "--model",
            "linear",
            "--cache-dir",
            str(tmp_path),
            "--out",
            str(out),
            "--live-window",
            "30",
        ]
    )
    assert TabularModel.load(str(out)).feature_names == list(LIVE_FEATURES)
    assert out.with_suffix(".report.json").exists()
    assert "trained TabularModel" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="no cached history"):
        train_cli(["--symbols", "HK.00005", "--cache-dir", str(tmp_path)])
