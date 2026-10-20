"""Tabular ML signal models: LightGBM, ridge/logistic and sklearn histogram GBM behind one wrapper.

``TabularModel`` trains on a feature frame and either a regression target (forward return) or a
classification target in {-1, 0, 1} (triple barrier). ``score`` returns a signed number per row
(the predicted return, or ``P(up) - P(down)``) for research; ``predict`` turns the last row into a
trading ``Prediction`` for the live ``SignalStage``.

Confidence is an uncalibrated monotone map of the score; calibrate it before it feeds sizing.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Final, Literal

import joblib
import numpy as np
import pandas as pd

from trader.model.base import ISignalModel, Prediction, Signal

Task = Literal["regression", "classification"]
EstimatorFactory = Callable[[Task, dict[str, Any]], Any]


def _lightgbm(task: Task, params: dict[str, Any]) -> Any:
    import lightgbm as lgb

    defaults: dict[str, Any] = {"verbose": -1, "random_state": 42, "n_jobs": 1}
    merged = defaults | params
    return lgb.LGBMRegressor(**merged) if task == "regression" else lgb.LGBMClassifier(**merged)


def _linear(task: Task, params: dict[str, Any]) -> Any:
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if task == "regression":
        return make_pipeline(StandardScaler(), Ridge(**params))
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, **params))


def _sklearn_gbm(task: Task, params: dict[str, Any]) -> Any:
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

    merged = {"random_state": 42} | params
    if task == "regression":
        return HistGradientBoostingRegressor(**merged)
    return HistGradientBoostingClassifier(**merged)


ESTIMATORS: Final[dict[str, EstimatorFactory]] = {
    "lightgbm": _lightgbm,
    "linear": _linear,
    "sklearn_gbm": _sklearn_gbm,
}


class TabularModel(ISignalModel):
    """One estimator from ``ESTIMATORS`` trained on named feature columns."""

    def __init__(
        self,
        estimator: str = "lightgbm",
        task: Task = "regression",
        params: dict[str, Any] | None = None,
        min_score: float = 0.0,
    ) -> None:
        """Create an unfitted model.

        Args:
            estimator: Key in ``ESTIMATORS``.
            task: ``regression`` for return targets, ``classification`` for {-1, 0, 1} labels.
            params: Estimator keyword arguments (the grid-search dimensions).
            min_score: Scores with ``|score| <= min_score`` map to HOLD.

        Raises:
            ValueError: On an unknown estimator.
        """
        if estimator not in ESTIMATORS:
            raise ValueError(f"unknown estimator {estimator!r}; known: {sorted(ESTIMATORS)}")
        self.estimator = estimator
        self.task: Task = task
        self.params = dict(params or {})
        self.min_score = min_score
        self.feature_names: list[str] = []
        self._model: Any = None
        self._score_scale = 1.0

    @property
    def is_fitted(self) -> bool:
        """Whether ``fit`` (or ``load``) has run."""
        return self._model is not None

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """Train on ``X`` (named columns) and ``y``.

        Raises:
            ValueError: On empty or misaligned inputs.
        """
        if X.empty or len(X) != len(y):
            raise ValueError(f"need matching non-empty X and y, got {len(X)} and {len(y)} rows")
        model = ESTIMATORS[self.estimator](self.task, self.params)
        target = y.astype(int) if self.task == "classification" else y.astype(float)
        model.fit(X, target)
        self._model = model
        self.feature_names = list(X.columns)
        in_sample = self._raw_score(X)
        spread = float(np.std(in_sample))
        self._score_scale = spread if spread > 0 else 1.0

    def score(self, X: pd.DataFrame) -> np.ndarray:
        """Signed score per row: predicted target, or ``P(up) - P(down)`` for classifiers.

        Raises:
            RuntimeError: When the model has not been fitted.
            KeyError: When ``X`` lacks a training feature.
        """
        if self._model is None:
            raise RuntimeError(f"{self.estimator} model is not fitted; train it or load one")
        return self._raw_score(X)

    def _raw_score(self, X: pd.DataFrame) -> np.ndarray:
        frame = X[self.feature_names] if self.feature_names else X
        if self.task == "regression":
            return np.asarray(self._model.predict(frame), dtype=float)
        probs = np.asarray(self._model.predict_proba(frame), dtype=float)
        classes = np.asarray(self._model.classes_, dtype=float)
        return probs @ np.sign(classes)

    def predict(self, features: pd.DataFrame) -> Prediction:
        """Signal for the last row; HOLD inside the ``min_score`` deadband."""
        score = float(self.score(features.tail(1))[0])
        if score > self.min_score:
            signal = Signal.BUY
        elif score < -self.min_score:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD
        confidence = math.tanh(abs(score) / self._score_scale)
        return Prediction(
            signal=signal,
            confidence=confidence,
            metadata={"score": score, "estimator": self.estimator, "task": self.task},
        )

    def save(self, path: str) -> None:
        """Serialise model, feature names and settings with joblib.

        Raises:
            RuntimeError: When the model has not been fitted.
        """
        if self._model is None:
            raise RuntimeError("refusing to save an unfitted model")
        joblib.dump(
            {
                "estimator": self.estimator,
                "task": self.task,
                "params": self.params,
                "min_score": self.min_score,
                "feature_names": self.feature_names,
                "score_scale": self._score_scale,
                "model": self._model,
            },
            path,
        )

    @classmethod
    def load(cls, path: str) -> TabularModel:
        """Load a model written by ``save``."""
        payload = joblib.load(path)
        model = cls(
            estimator=payload["estimator"],
            task=payload["task"],
            params=payload["params"],
            min_score=payload["min_score"],
        )
        model.feature_names = list(payload["feature_names"])
        model._score_scale = float(payload["score_scale"])
        model._model = payload["model"]
        return model
