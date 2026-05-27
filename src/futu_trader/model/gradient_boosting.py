"""Gradient boosting model wrapper."""

from __future__ import annotations

import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier

from futu_trader.model.base import ISignalModel, Prediction, Signal


class GradientBoostingModel(ISignalModel):
    """Sklearn GradientBoosting classifier wrapper."""

    def __init__(self, random_state: int = 42) -> None:
        self.model = GradientBoostingClassifier(random_state=random_state)

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """Train classifier."""
        self.model.fit(X, y)

    def predict(self, features: pd.DataFrame) -> Prediction:
        """Predict class and confidence."""
        probs = self.model.predict_proba(features.tail(1))[0]
        index = int(probs.argmax())
        labels = [Signal.SELL, Signal.HOLD, Signal.BUY]
        return Prediction(
            signal=labels[index], confidence=float(probs[index]), metadata={"probs": probs.tolist()}
        )

    def save(self, path: str) -> None:
        """Serialize model with joblib."""
        joblib.dump(self.model, path)

    @classmethod
    def load(cls, path: str) -> GradientBoostingModel:
        """Load model artifact."""
        instance = cls()
        instance.model = joblib.load(path)
        return instance
