"""Mean reversion model."""

from __future__ import annotations

import json

import pandas as pd

from futu_trader.model.base import ISignalModel, Prediction, Signal


# Idea of MeanReversionModel,
# asset prices tend to revert to their historical mean over time.
class MeanReversionModel(ISignalModel):
    """Z-score threshold model."""

    def __init__(self, buy_threshold: float = -2.0, sell_threshold: float = 2.0) -> None:
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

    def predict(self, features: pd.DataFrame) -> Prediction:
        """Predict using latest z-score."""
        z_score = float(features["z_score"].iloc[-1])
        if z_score < self.buy_threshold:
            signal = Signal.BUY
        elif z_score > self.sell_threshold:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD
        confidence = min(abs(z_score) / 3.0, 1.0)
        return Prediction(signal=signal, confidence=confidence, metadata={"z_score": z_score})

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """No-op for rule model."""

    def save(self, path: str) -> None:
        """Save config to JSON."""
        payload = {"buy_threshold": self.buy_threshold, "sell_threshold": self.sell_threshold}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    @classmethod
    def load(cls, path: str) -> MeanReversionModel:
        """Load model config from JSON."""
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls(**payload)
