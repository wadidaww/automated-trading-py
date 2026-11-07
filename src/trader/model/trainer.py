"""Model training orchestration."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from trader.model.base import ISignalModel


@dataclass(slots=True)
class TrainingReport:
    """Serializable training report."""

    model_name: str
    n_samples: int


class ModelTrainer:
    """Train models and emit report."""

    def train(self, model: ISignalModel, X: pd.DataFrame, y: pd.Series) -> TrainingReport:
        """Train model and report what was trained on.

        Performance metrics come from an out-of-sample backtest, not from here.

        Raises:
            ValueError: When there is no training data or features and labels disagree.
        """
        if X.empty or len(X) != len(y):
            raise ValueError(f"need matching non-empty X and y, got {len(X)} and {len(y)} rows")
        model.fit(X, y)
        return TrainingReport(model_name=model.__class__.__name__, n_samples=len(X))

    def save_report(self, report: TrainingReport, path: str) -> None:
        """Save report to JSON."""
        Path(path).write_text(json.dumps(asdict(report)), encoding="utf-8")


def _cli(argv: list[str] | None = None) -> None:
    """Train one tabular model on cached history and save it for the live pipeline.

    Defaults train on the ``live`` feature group so the artifact runs in ``SignalStage``; use
    ``trader.research.grid_search`` to choose features, label and hyperparameters first.
    """
    from trader.data.features import FeatureSpec
    from trader.data.fetcher import load_history
    from trader.model.tabular import TabularModel
    from trader.research.dataset import build_dataset
    from trader.research.labels import LabelSpec

    parser = argparse.ArgumentParser(description="Train a model on cached history")
    parser.add_argument("--model", default="lightgbm", help="lightgbm, linear or sklearn_gbm")
    parser.add_argument("--symbols", default="HK.00700", help="comma-separated Futu codes")
    parser.add_argument("--features", default="live", help="comma-separated feature groups")
    parser.add_argument("--live-window", type=int, default=100)
    parser.add_argument(
        "--label", default="forward_return", choices=["forward_return", "triple_barrier"]
    )
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--params", default="{}", help="estimator params as JSON")
    parser.add_argument("--cache-dir", default="data/raw")
    parser.add_argument("--ktype", default="K_DAY")
    parser.add_argument("--out", default="data/models/model.pkl")
    args = parser.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    try:
        bars = {s: load_history(s, args.ktype, args.cache_dir) for s in symbols}
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    features = FeatureSpec(
        groups=tuple(g.strip() for g in args.features.split(",")), live_window=args.live_window
    )
    label = LabelSpec(kind=args.label, horizon=args.horizon)
    dataset = build_dataset(bars, features, label)
    model = TabularModel(estimator=args.model, task=label.task, params=json.loads(args.params))
    report = ModelTrainer().train(model, dataset.X, dataset.y)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(out))
    ModelTrainer().save_report(report, str(out.with_suffix(".report.json")))
    print(f"trained {report.model_name} ({args.model}) on {report.n_samples} rows -> {out}")


if __name__ == "__main__":
    _cli()
