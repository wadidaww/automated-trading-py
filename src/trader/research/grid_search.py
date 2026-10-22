"""Grid search over feature sets, labels, models and hyperparameters.

Run ``PYTHONPATH=src python -m trader.research.grid_search --spec config/research.example.yaml``.

Every combination in the spec is scored with purged walk-forward validation and logged to
``<output.dir>/<run id>/trials.jsonl`` (params, data hash, code SHA, metrics). The run ends with a
leaderboard and the deflated Sharpe ratio of the winner, which accounts for how many trials were
tried. With ``output.refit_best`` the winner is refit on all data and saved for the trainer or, if
it uses the ``live`` feature group, for the live ``SignalStage``.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import subprocess
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, Field

from trader.data.features import FeatureSpec
from trader.data.fetcher import load_history
from trader.model.tabular import ESTIMATORS, TabularModel
from trader.research.dataset import Dataset, build_dataset
from trader.research.experiment import (
    BacktestSettings,
    TrialMetrics,
    ValidationSettings,
    out_of_sample_scores,
    score_predictions,
)
from trader.research.labels import LabelSpec
from trader.research.scoring import deflated_sharpe_ratio
from trader.research.synthetic import synthetic_bars

# Feature/label fields whose single value is itself a list; a grid over them is a list of lists.
_LIST_FIELDS = frozenset({"groups", "windows"})


class DataSection(BaseModel):
    """Where bars come from."""

    source: Literal["csv", "synthetic"] = "csv"
    symbols: list[str] = Field(default_factory=lambda: ["HK.00700"])
    ktype: str = "K_DAY"
    cache_dir: str = "data/raw"
    start: date | None = None
    end: date | None = None
    synthetic_bars: int = 1000
    synthetic_autocorrelation: float = 0.1


class ModelSection(BaseModel):
    """One estimator and its hyperparameter grid (each value a list of options)."""

    estimator: str
    params: dict[str, Any] = Field(default_factory=dict)


class ValidationSection(BaseModel):
    """Walk-forward settings."""

    n_splits: int = 5
    embargo: int = 5
    min_train: int = 252
    expanding: bool = True


class BacktestSection(BaseModel):
    """Strategy scoring settings."""

    cost_bps: float = 20.0
    long_only: bool = True
    deadband: float = 0.0
    periods_per_year: int = 252


class OutputSection(BaseModel):
    """Where results go and how the winner is picked."""

    dir: str = "data/research"
    select_by: Literal["sharpe", "rank_ic", "icir"] = "sharpe"
    refit_best: bool = True


def _one_default() -> list[dict[str, Any]]:
    """A single option with every field at its default."""
    return [{}]


class GridSpec(BaseModel):
    """The whole search, as loaded from YAML."""

    data: DataSection = Field(default_factory=DataSection)
    features: list[dict[str, Any]] = Field(default_factory=_one_default)
    labels: list[dict[str, Any]] = Field(default_factory=_one_default)
    rank_features: list[bool] = Field(default_factory=lambda: [False])
    models: list[ModelSection] = Field(default_factory=lambda: [ModelSection(estimator="lightgbm")])
    validation: ValidationSection = Field(default_factory=ValidationSection)
    backtest: BacktestSection = Field(default_factory=BacktestSection)
    output: OutputSection = Field(default_factory=OutputSection)


@dataclass(frozen=True, slots=True)
class Trial:
    """One point of the grid."""

    trial_id: int
    features: FeatureSpec
    label: LabelSpec
    rank_features: bool
    estimator: str
    params: dict[str, Any]

    def describe(self) -> dict[str, Any]:
        """JSON-friendly description of the configuration."""
        return {
            "trial_id": self.trial_id,
            "features": asdict(self.features),
            "label": asdict(self.label),
            "rank_features": self.rank_features,
            "estimator": self.estimator,
            "params": self.params,
        }


def expand(
    options: dict[str, Any], list_fields: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """Cartesian product of a dict whose list values are alternatives.

    A field in ``list_fields`` holds a list as its single value, so only a list of lists is
    expanded there: ``{"windows": [[5, 20], [10, 60]]}`` gives two options.
    """
    keys = list(options)
    choices: list[list[Any]] = []
    for key in keys:
        value = options[key]
        if key in list_fields:
            nested = isinstance(value, list) and value and isinstance(value[0], list)
            choices.append(value if nested else [value])
        else:
            choices.append(value if isinstance(value, list) else [value])
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*choices)]


def _feature_spec(raw: dict[str, Any]) -> FeatureSpec:
    values = dict(raw)
    for key in ("groups", "windows"):
        if key in values:
            values[key] = tuple(values[key])
    return FeatureSpec(**values)


def build_trials(spec: GridSpec) -> list[Trial]:
    """Every combination of features x labels x ranking x model x hyperparameters.

    Raises:
        ValueError: On an unknown estimator.
    """
    features = [_feature_spec(o) for raw in spec.features for o in expand(raw, _LIST_FIELDS)]
    labels = [LabelSpec(**o) for raw in spec.labels for o in expand(raw)]
    models: list[tuple[str, dict[str, Any]]] = []
    for section in spec.models:
        if section.estimator not in ESTIMATORS:
            raise ValueError(
                f"unknown estimator {section.estimator!r}; known: {sorted(ESTIMATORS)}"
            )
        models.extend((section.estimator, params) for params in expand(section.params))
    combos = itertools.product(features, labels, spec.rank_features, models)
    return [
        Trial(trial_id, feat, label, rank, estimator, params)
        for trial_id, (feat, label, rank, (estimator, params)) in enumerate(combos)
    ]


def load_bars(section: DataSection) -> dict[str, pd.DataFrame]:
    """Load cached history (or synthetic bars) per symbol, clipped to ``start``/``end``."""
    bars: dict[str, pd.DataFrame] = {}
    for offset, symbol in enumerate(section.symbols):
        if section.source == "synthetic":
            frame = synthetic_bars(
                n_bars=section.synthetic_bars,
                seed=offset,
                autocorrelation=section.synthetic_autocorrelation,
            )
        else:
            frame = load_history(symbol, section.ktype, section.cache_dir)
        if section.start is not None:
            frame = frame[frame.index >= pd.Timestamp(section.start)]
        if section.end is not None:
            frame = frame[frame.index <= pd.Timestamp(section.end)]
        bars[symbol] = frame
    return bars


def data_hash(bars: dict[str, pd.DataFrame]) -> str:
    """Short content hash of the input bars, logged with every trial."""
    digest = hashlib.sha256()
    for symbol in sorted(bars):
        digest.update(symbol.encode())
        digest.update(pd.util.hash_pandas_object(bars[symbol]).to_numpy().tobytes())
    return digest.hexdigest()[:16]


def code_sha() -> str:
    """Current git commit, or ``unknown`` outside a checkout."""
    try:
        # Fixed argv, no user input.
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S603, S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


@dataclass(slots=True)
class TrialRecord:
    """A finished trial: its configuration and out-of-sample metrics (or the error)."""

    trial: Trial
    metrics: TrialMetrics | None
    error: str | None = None

    def selection_value(self, select_by: str) -> float:
        """The metric used to rank trials; failed or undefined trials sort last."""
        if self.metrics is None:
            return -math.inf
        value = float(getattr(self.metrics, select_by))
        return value if math.isfinite(value) else -math.inf


class GridSearch:
    """Run every trial of a ``GridSpec`` and write the registry and summary."""

    def __init__(self, spec: GridSpec, run_dir: Path | None = None) -> None:
        self.spec = spec
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = run_dir or Path(spec.output.dir) / stamp
        self._datasets: dict[tuple[str, str, bool], Dataset] = {}

    def _dataset(self, bars: dict[str, pd.DataFrame], trial: Trial) -> Dataset:
        key = (trial.features.key, trial.label.key, trial.rank_features)
        if key not in self._datasets:
            self._datasets[key] = build_dataset(
                bars, trial.features, trial.label, trial.rank_features
            )
        return self._datasets[key]

    def run(self, max_trials: int | None = None) -> dict[str, Any]:
        """Evaluate the grid; returns the summary that is also written to ``summary.json``."""
        trials = build_trials(self.spec)[:max_trials]
        bars = load_bars(self.spec.data)
        meta = {"data_hash": data_hash(bars), "code_sha": code_sha()}
        validation = ValidationSettings(**self.spec.validation.model_dump())
        backtest = BacktestSettings(**self.spec.backtest.model_dump())
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "spec.json").write_text(self.spec.model_dump_json(indent=2), "utf-8")
        records: list[TrialRecord] = []
        with (self.run_dir / "trials.jsonl").open("w", encoding="utf-8") as registry:
            for trial in trials:
                record = self._evaluate(bars, trial, validation, backtest)
                records.append(record)
                entry = (
                    trial.describe()
                    | meta
                    | {
                        "metrics": record.metrics.to_dict() if record.metrics else None,
                        "error": record.error,
                    }
                )
                registry.write(json.dumps(entry, default=str) + "\n")
                registry.flush()
                _print_progress(record, len(trials))
        summary = self._summarise(records, meta)
        if self.spec.output.refit_best and summary.get("best") is not None:
            summary["best_model"] = self._refit(bars, records, summary["best"]["trial_id"])
        (self.run_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, default=str), "utf-8"
        )
        _leaderboard(records, self.spec.output.select_by).to_csv(
            self.run_dir / "leaderboard.csv", index=False
        )
        return summary

    def _evaluate(
        self,
        bars: dict[str, pd.DataFrame],
        trial: Trial,
        validation: ValidationSettings,
        backtest: BacktestSettings,
    ) -> TrialRecord:
        try:
            dataset = self._dataset(bars, trial)
            scores, folds = out_of_sample_scores(
                dataset,
                trial.estimator,
                trial.label.task,
                trial.params,
                trial.label.horizon,
                validation,
            )
            return TrialRecord(trial, score_predictions(dataset, scores, folds, backtest))
        except (ValueError, TypeError, KeyError) as exc:
            return TrialRecord(trial, None, f"{type(exc).__name__}: {exc}")

    def _summarise(self, records: list[TrialRecord], meta: dict[str, str]) -> dict[str, Any]:
        select_by = self.spec.output.select_by
        scored = [r for r in records if r.metrics is not None]
        summary: dict[str, Any] = {
            "run_dir": str(self.run_dir),
            "n_trials": len(records),
            "n_failed": len(records) - len(scored),
            "select_by": select_by,
            **meta,
            "best": None,
        }
        if not scored:
            return summary
        best = max(scored, key=lambda r: r.selection_value(select_by))
        sharpes = np.array(
            [r.metrics.sharpe for r in scored if r.metrics and math.isfinite(r.metrics.sharpe)]
        )
        assert best.metrics is not None
        dsr = deflated_sharpe_ratio(
            best.metrics.sharpe,
            best.metrics.n_obs,
            best.metrics.skew,
            best.metrics.kurtosis,
            float(sharpes.var(ddof=1)) if len(sharpes) > 1 else 0.0,
            len(records),
        )
        summary["best"] = best.trial.describe() | {"metrics": best.metrics.to_dict()}
        summary["deflated_sharpe"] = None if math.isnan(dsr) else dsr
        return summary

    def _refit(
        self, bars: dict[str, pd.DataFrame], records: list[TrialRecord], trial_id: int
    ) -> dict[str, Any]:
        trial = next(r.trial for r in records if r.trial.trial_id == trial_id)
        dataset = self._dataset(bars, trial)
        model = TabularModel(estimator=trial.estimator, task=trial.label.task, params=trial.params)
        model.fit(dataset.X, dataset.y)
        path = self.run_dir / "best_model.joblib"
        model.save(str(path))
        live_ready = trial.features.groups == ("live",) and not trial.rank_features
        return {
            "path": str(path),
            "feature_names": model.feature_names,
            "live_compatible": live_ready,
        }


def _print_progress(record: TrialRecord, total: int) -> None:
    trial = record.trial
    head = (
        f"[{trial.trial_id + 1}/{total}] {trial.estimator} {trial.params} "
        f"{trial.label.key} {trial.features.key}"
    )
    if record.metrics is None:
        print(f"{head} -> FAILED {record.error}")
        return
    m = record.metrics
    print(
        f"{head} -> rank_ic={m.rank_ic:.4f} sharpe_net={m.sharpe_annual:.2f} "
        f"sharpe_gross={m.sharpe_annual_gross:.2f} turnover={m.turnover:.2f}"
    )


def _leaderboard(records: list[TrialRecord], select_by: str) -> pd.DataFrame:
    rows = []
    for record in sorted(records, key=lambda r: r.selection_value(select_by), reverse=True):
        trial = record.trial
        rows.append(
            {
                "trial_id": trial.trial_id,
                "estimator": trial.estimator,
                "params": json.dumps(trial.params, sort_keys=True),
                "features": trial.features.key,
                "label": trial.label.key,
                "rank_features": trial.rank_features,
                **(record.metrics.to_dict() if record.metrics else {"error": record.error}),
            }
        )
    return pd.DataFrame(rows)


def iter_trial_lines(trials: list[Trial]) -> Iterator[str]:
    """Human-readable trial list for ``--dry-run``."""
    for trial in trials:
        yield json.dumps(trial.describe(), default=str)


def load_spec(path: str) -> GridSpec:
    """Parse and validate a grid spec YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return GridSpec.model_validate(raw)


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Grid search features, labels and models")
    parser.add_argument("--spec", default="config/research.example.yaml")
    parser.add_argument("--max-trials", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="list the trials and exit")
    parser.add_argument(
        "--synthetic", action="store_true", help="use synthetic bars instead of the CSV cache"
    )
    args = parser.parse_args(argv)
    spec = load_spec(args.spec)
    if args.synthetic:
        spec.data.source = "synthetic"
    trials = build_trials(spec)
    if args.dry_run:
        for line in iter_trial_lines(trials[: args.max_trials]):
            print(line)
        print(f"{min(len(trials), args.max_trials or len(trials))} trials")
        return
    summary = GridSearch(spec).run(args.max_trials)
    print(json.dumps(summary, indent=2, default=str))
    if summary["best"] is None:
        raise SystemExit("every trial failed; see trials.jsonl")


if __name__ == "__main__":
    main()
