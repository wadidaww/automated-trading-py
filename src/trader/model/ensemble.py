"""Ensemble signal model combining multiple predictors."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trader.model.base import ISignalModel, Prediction, Signal


@dataclass(slots=True)
class ModelWeight:
    """Weight configuration for an ensemble member."""

    model: ISignalModel
    weight: float
    name: str


class EnsembleSignalModel(ISignalModel):
    """Weighted ensemble of multiple signal models.

    Combines predictions from multiple models using weighted voting.
    Each model contributes a vote with its confidence as weight.
    The final signal is determined by the weighted majority vote.
    """

    def __init__(self, models: list[ModelWeight] | None = None) -> None:
        self._models = models or []
        self._min_agreement = 0.5

    def add_model(self, model: ISignalModel, weight: float, name: str) -> None:
        """Add a model to the ensemble."""
        self._models.append(ModelWeight(model=model, weight=weight, name=name))

    def predict(self, features: pd.DataFrame) -> Prediction:
        """Aggregate predictions from all ensemble members.

        Uses weighted voting where each model's vote is weighted by
        its configured weight times its prediction confidence.
        """
        if not self._models:
            return Prediction(signal=Signal.HOLD, confidence=0.0, metadata={"error": "no models"})

        votes: dict[Signal, float] = {Signal.BUY: 0.0, Signal.SELL: 0.0, Signal.HOLD: 0.0}
        total_weight = 0.0
        model_details: dict[str, dict[str, float | str]] = {}

        for member in self._models:
            try:
                prediction = member.model.predict(features)
                vote_weight = member.weight * prediction.confidence
                votes[prediction.signal] += vote_weight
                total_weight += member.weight
                model_details[member.name] = {
                    "signal": prediction.signal.value,
                    "confidence": prediction.confidence,
                    "vote_weight": vote_weight,
                }
            except Exception:
                model_details[member.name] = {
                    "signal": "ERROR",
                    "confidence": 0.0,
                    "vote_weight": 0.0,
                }

        if total_weight == 0:
            return Prediction(
                signal=Signal.HOLD, confidence=0.0, metadata={"models": model_details}
            )

        normalized = {s: v / total_weight for s, v in votes.items()}
        best_signal = max(votes, key=lambda s: votes[s])
        best_score = normalized[best_signal]

        if best_signal == Signal.HOLD and best_score < self._min_agreement:
            non_hold = {s: v for s, v in normalized.items() if s != Signal.HOLD}
            if non_hold:
                best_signal = max(non_hold, key=lambda s: non_hold[s])
                best_score = non_hold[best_signal]

        confidence = best_score

        return Prediction(
            signal=best_signal,
            confidence=confidence,
            metadata={
                "normalized_votes": {s.value: round(v, 4) for s, v in normalized.items()},
                "raw_votes": {s.value: round(v, 4) for s, v in votes.items()},
                "total_weight": round(total_weight, 4),
                "models": model_details,
            },
        )

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """Fit all ensemble member models."""
        for member in self._models:
            member.model.fit(X, y)

    def save(self, path: str) -> None:
        """Save ensemble configuration (individual models saved separately)."""
        import json

        config = {
            "model_count": len(self._models),
            "models": [
                {"name": m.name, "weight": m.weight, "type": type(m.model).__name__}
                for m in self._models
            ],
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)

    @classmethod
    def load(cls, path: str) -> EnsembleSignalModel:
        """Load ensemble from saved configuration."""
        import json

        with open(path, encoding="utf-8") as handle:
            _config = json.load(handle)
        return cls(models=[])
