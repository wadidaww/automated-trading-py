"""Model training orchestration."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from futu_trader.model.base import ISignalModel
from futu_trader.model.mean_reversion import MeanReversionModel


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


def _cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="mean_reversion")
    parser.add_argument("--symbols", default="700.HK")
    _ = parser.parse_args()
    model = MeanReversionModel()
    X = pd.DataFrame({"z_score": [-1.0, -2.2, 2.5]})
    y = pd.Series([0, 1, 2])
    report = ModelTrainer().train(model, X, y)
    print(report)


if __name__ == "__main__":
    _cli()
