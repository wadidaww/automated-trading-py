"""Moving-average trend model: hold the symbol while its close is above its long average.

Rule-based, nothing is fitted. The close is compared with its ``model.trend.sma_window`` bar
simple average (200 daily bars by default, the Faber 2007 timing rule): above it the target
exposure is 1, at or below it 0. Like the trend model it states a *target exposure*, so sizing,
the move from the current position to the target and every limit are the pre-trade risk
service's job (``risk.vol_target`` must be enabled). A missing or non-finite feature gives HOLD.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from trader.model.base import ISignalModel, Prediction, Signal

FEATURE = "sma_gap"
MODEL_NAME = "sma_trend"


class SmaTrendModel(ISignalModel):
    """Long-only timing model over the ``sma_gap`` feature computed by ``DataStage``."""

    def predict(self, features: pd.DataFrame) -> Prediction:
        """Target exposure from the last feature row; HOLD when ``sma_gap`` is missing."""
        gap = _last(features, FEATURE)
        if not math.isfinite(gap):
            return Prediction(Signal.HOLD, 0.0, {"reason": "trend_features_unavailable"})
        exposure = 1.0 if gap > 0 else 0.0
        return Prediction(
            signal=Signal.BUY if exposure > 0 else Signal.SELL,
            # A rule, not a probability: the confidence only says the input was complete.
            confidence=1.0,
            metadata={FEATURE: gap, "target_exposure": exposure},
            target_exposure=exposure,
        )

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """Nothing to fit: the rule has no learned parameters."""

    def save(self, path: str) -> None:
        """Write a marker file so the artifact round-trips like other models."""
        Path(path).write_text(json.dumps({"model": MODEL_NAME}))

    @classmethod
    def load(cls, path: str) -> SmaTrendModel:
        """Load the marker written by :meth:`save`.

        Raises:
            ValueError: When the file is not an ``sma_trend`` marker.
        """
        payload = json.loads(Path(path).read_text())
        if payload.get("model") != MODEL_NAME:
            raise ValueError(f"{path} is not an sma_trend model artifact")
        return cls()


def _last(features: pd.DataFrame, column: str) -> float:
    if column not in features.columns or features.empty:
        return math.nan
    try:
        return float(features[column].iloc[-1])
    except (TypeError, ValueError):
        return math.nan
