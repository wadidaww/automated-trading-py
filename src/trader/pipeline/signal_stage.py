"""Signal stage."""

from __future__ import annotations

import time
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
    """Generate trade signals with confidence threshold and cooldown.

    Enforces a minimum time gap between consecutive signals for the same
    symbol to prevent overtrading and reduce transaction costs.
    """

    def __init__(
        self,
        model: ISignalModel,
        confidence_threshold: float = 0.65,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.model = model
        self.confidence_threshold = confidence_threshold
        self._cooldown_seconds = cooldown_seconds
        self._last_signal_time: dict[str, float] = {}

    async def process(self, item: FeatureWindow) -> TradeSignal | None:
        """Generate trade signal with cooldown enforcement."""
        now = time.time()
        last_time = self._last_signal_time.get(item.symbol, 0.0)
        if now - last_time < self._cooldown_seconds:
            return None

        features = pd.DataFrame(
            {
                "z_score": [item.z_score],
                "momentum": [item.momentum],
                "trend_strength": [item.trend_strength],
                "volatility": [item.volatility],
                "rsi": [item.rsi],
                "bb_position": [item.bb_position],
                "price_acceleration": [item.price_acceleration],
            }
        )
        prediction = self.model.predict(features)
        if prediction.confidence < self.confidence_threshold:
            return None

        self._last_signal_time[item.symbol] = now
        return TradeSignal(
            symbol=item.symbol,
            signal=prediction.signal,
            confidence=prediction.confidence,
            price=item.price,
        )
