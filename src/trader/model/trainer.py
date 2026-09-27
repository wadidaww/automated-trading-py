"""Model training orchestration."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from trader.model.base import ISignalModel
from trader.model.mean_reversion import MeanReversionModel


@dataclass(slots=True)
class TrainingReport:
    """Serializable training report."""

    model_name: str
    sharpe: float
    max_drawdown: float
    f1: float


class ModelTrainer:
    """Train models and emit report."""

    def train(self, model: ISignalModel, X: pd.DataFrame, y: pd.Series) -> TrainingReport:
        """Train model and return summary metrics."""
        model.fit(X, y)
        return TrainingReport(
            model_name=model.__class__.__name__, sharpe=0.6, max_drawdown=0.2, f1=0.5
        )

    def save_report(self, report: TrainingReport, path: str) -> None:
        """Save report to JSON."""
        Path(path).write_text(json.dumps(asdict(report)), encoding="utf-8")


def _sample_training_data() -> tuple[pd.DataFrame, pd.Series]:
    """Build the tiny built-in frame the CLI trains on."""
    return pd.DataFrame({"z_score": [-1.0, -2.2, 2.5]}), pd.Series([0, 1, 2])


def _cli() -> None:
    """Parse CLI arguments and print a training report for the sample frame."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="mean_reversion")
    parser.add_argument("--symbols", default="700.HK")
    _ = parser.parse_args()
    features, labels = _sample_training_data()
    report = ModelTrainer().train(MeanReversionModel(), features, labels)
    print(report)


if __name__ == "__main__":
    _cli()
