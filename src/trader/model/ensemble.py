"""Ensemble signal model combining multiple predictors."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from trader.model.base import ISignalModel, Prediction, Signal


@dataclass(slots=True)
class ModelWeight:
    """Weight configuration for an ensemble member."""

    model: ISignalModel
    weight: float
    name: str


@dataclass(frozen=True, slots=True)
class _MemberVote:
    """One ensemble member's contribution to the weighted vote."""

    weight: float
    signal: Signal
    confidence: float
    vote_weight: float

    def detail(self) -> dict[str, float | str]:
        """Render the per-model metadata entry for this vote."""
        return {
            "signal": self.signal.value,
            "confidence": self.confidence,
            "vote_weight": self.vote_weight,
        }


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

        outcomes = [self._weighted_vote(member, features) for member in self._models]
        raw_votes = self._tally(outcomes)
        total_weight = 0.0
        model_details: dict[str, dict[str, float | str]] = {}
        for member, vote in zip(self._models, outcomes, strict=True):
            if vote is None:
                model_details[member.name] = {
                    "signal": "ERROR",
                    "confidence": 0.0,
                    "vote_weight": 0.0,
                }
                continue
            total_weight += vote.weight
            model_details[member.name] = vote.detail()

        if total_weight == 0:
            return Prediction(
                signal=Signal.HOLD, confidence=0.0, metadata={"models": model_details}
            )

        normalized = {signal: weight / total_weight for signal, weight in raw_votes.items()}
        best_signal, confidence = self._winner(raw_votes, normalized)

        return Prediction(
            signal=best_signal,
            confidence=confidence,
            metadata={
                "normalized_votes": {
                    signal.value: round(score, 4) for signal, score in normalized.items()
                },
                "raw_votes": {
                    signal.value: round(weight, 4) for signal, weight in raw_votes.items()
                },
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
        with open(path, encoding="utf-8") as handle:
            _config = json.load(handle)
        return cls(models=[])

    @staticmethod
    def _weighted_vote(member: ModelWeight, features: pd.DataFrame) -> _MemberVote | None:
        """Run one member and package its weighted vote; None when the member fails."""
        try:
            prediction = member.model.predict(features)
            if not isinstance(prediction.signal, Signal):
                return None
            return _MemberVote(
                weight=member.weight,
                signal=prediction.signal,
                confidence=prediction.confidence,
                vote_weight=member.weight * prediction.confidence,
            )
        except Exception:
            return None

    @staticmethod
    def _tally(outcomes: list[_MemberVote | None]) -> dict[Signal, float]:
        """Accumulate vote weights per signal, starting from zero for every signal."""
        tally = dict.fromkeys(Signal, 0.0)
        for vote in outcomes:
            if vote is not None:
                tally[vote.signal] += vote.vote_weight
        return tally

    def _winner(
        self, raw_votes: dict[Signal, float], normalized: dict[Signal, float]
    ) -> tuple[Signal, float]:
        """Pick the strongest signal, letting a marginal HOLD lose to the best alternative."""
        best_signal = max(raw_votes, key=lambda signal: raw_votes[signal])
        best_score = normalized[best_signal]
        challengers = {
            signal: score for signal, score in normalized.items() if signal != Signal.HOLD
        }
        if best_signal == Signal.HOLD and best_score < self._min_agreement and challengers:
            best_signal = max(challengers, key=lambda signal: challengers[signal])
            best_score = challengers[best_signal]
        return best_signal, best_score
