"""Signal stage."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trader.model.base import ISignalModel, Signal
from trader.pipeline.base import IStage
from trader.pipeline.data_stage import FeatureWindow


@dataclass(slots=True)
class TradeSignal:
    """Signal ready for risk checks."""

    symbol: str
    signal: Signal
    confidence: float
    price: float


class SignalStage(IStage[FeatureWindow, TradeSignal | None]):
    """Generate trade signals with confidence threshold."""

    def __init__(self, model: ISignalModel, confidence_threshold: float = 0.65) -> None:
        self.model = model
        self.confidence_threshold = confidence_threshold

    async def process(self, item: FeatureWindow) -> TradeSignal | None:
        """Generate trade signal."""
        prediction = self.model.predict(pd.DataFrame({"z_score": [item.z_score]}))
        if prediction.confidence < self.confidence_threshold:
            return None
        return TradeSignal(
            symbol=item.symbol,
            signal=prediction.signal,
            confidence=prediction.confidence,
            price=item.price,
        )
