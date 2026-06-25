"""Base interfaces for signal models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd


class Signal(Enum):
    """Trade signal values."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(slots=True)
class Prediction:
    """Model prediction payload."""

    signal: Signal
    confidence: float
    metadata: dict[str, Any]


class ISignalModel(ABC):
    """Signal model interface."""

    @abstractmethod
    def predict(self, features: pd.DataFrame) -> Prediction:
        """Predict market signal from feature window."""

    @abstractmethod
    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """Fit model on training data."""

    @abstractmethod
    def save(self, path: str) -> None:
        """Save model artifact to path."""

    @classmethod
    @abstractmethod
    def load(cls, path: str) -> ISignalModel:
        """Load model artifact."""
