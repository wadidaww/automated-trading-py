"""Trend-following signal model: blended long-horizon return plus a Donchian breakout vote.

Rule-based, nothing is fitted. The two votes each carry half of the target exposure, so no single
lookback decides alone:

- the mean of the 3, 6 and 12 month returns is positive (Moskowitz, Ooi, Pedersen 2012);
- the Donchian channel state is long (55-bar breakout, 20-bar exit).

The model states a *target exposure* (0, 0.5 or 1) of the symbol's risk budget, not a one-off
trade: BUY means "hold this much", SELL means "hold nothing". Sizing by volatility, the move from
the current position to the target, and every limit are the pre-trade risk service's job
(``risk.vol_target`` must be enabled). Missing or non-finite features give HOLD.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from trader.model.base import ISignalModel, Prediction, Signal

VOTE_WEIGHT = 0.5


class TrendFollowingModel(ISignalModel):
    """Long-only trend model over the ``TREND_FEATURES`` computed by ``DataStage``."""

    def predict(self, features: pd.DataFrame) -> Prediction:
        """Target exposure from the last feature row; HOLD when a trend feature is missing."""
        trend_return = _last(features, "trend_return")
        donchian = _last(features, "donchian_long")
        if not math.isfinite(trend_return) or not math.isfinite(donchian):
            return Prediction(Signal.HOLD, 0.0, {"reason": "trend_features_unavailable"})
        return_vote = trend_return > 0
        donchian_vote = donchian >= 0.5
        exposure = VOTE_WEIGHT * return_vote + VOTE_WEIGHT * donchian_vote
        return Prediction(
            signal=Signal.BUY if exposure > 0 else Signal.SELL,
            # A rule, not a probability: the confidence only says the inputs were complete.
            confidence=1.0,
            metadata={
                "trend_return": trend_return,
                "return_vote": return_vote,
                "donchian_vote": donchian_vote,
                "target_exposure": exposure,
            },
            target_exposure=exposure,
        )

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """Nothing to fit: the rule has no learned parameters."""

    def save(self, path: str) -> None:
        """Write a marker file so the artifact round-trips like other models."""
        Path(path).write_text(json.dumps({"model": "trend", "vote_weight": VOTE_WEIGHT}))

    @classmethod
    def load(cls, path: str) -> TrendFollowingModel:
        """Load the marker written by :meth:`save`.

        Raises:
            ValueError: When the file is not a trend model marker.
        """
        payload = json.loads(Path(path).read_text())
        if payload.get("model") != "trend":
            raise ValueError(f"{path} is not a trend model artifact")
        return cls()


def _last(features: pd.DataFrame, column: str) -> float:
    if column not in features.columns or features.empty:
        return math.nan
    try:
        return float(features[column].iloc[-1])
    except (TypeError, ValueError):
        return math.nan
