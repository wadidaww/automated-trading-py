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


def _cli() -> None:
    """Parse CLI arguments and fail loudly: there is no historical data loader yet."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="mean_reversion")
    parser.add_argument("--symbols", default="HK.00700")
    parser.parse_args()
    raise SystemExit(
        "no training data: the historical loader (trader.data.fetcher) is not implemented yet"
    )


if __name__ == "__main__":
    _cli()
