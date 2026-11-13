"""Signal stage."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

import pandas as pd

from trader.data.features import LIVE_FEATURES
from trader.model.base import ISignalModel, Signal
from trader.pipeline.base import IStage
from trader.pipeline.data_stage import FeatureWindow
from trader.utils.logger import get_logger

logger = get_logger("signal_stage")

FEATURE_COLUMNS: Final[tuple[str, ...]] = LIVE_FEATURES


@dataclass(slots=True)
class TradeSignal:
    """Signal ready for risk checks."""

    symbol: str
    signal: Signal
    confidence: float
    price: float
    # Set by target-position models (trend): the wanted share of the symbol's risk budget and the
    # realised volatility the risk stage sizes it with.
    target_exposure: float | None = None
    realised_vol: float | None = None


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
        feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
    ) -> None:
        self.model = model
        # Models fitted on the live features must see exactly those columns; only the trend
        # model's stage adds the trend features.
        self._feature_columns = feature_columns
        self.confidence_threshold = confidence_threshold
        self._cooldown_seconds = cooldown_seconds
        self._last_signal_time: dict[str, float] = {}

    async def process(self, item: FeatureWindow) -> TradeSignal | None:
        """Generate trade signal with cooldown enforcement."""
        now = time.time()
        last_time = self._last_signal_time.get(item.symbol, 0.0)
        if now - last_time < self._cooldown_seconds:
            logger.info(
                "signal_cooldown",
                symbol=item.symbol,
                remaining_s=round(self._cooldown_seconds - (now - last_time), 1),
            )
            return None

        features = pd.DataFrame({name: [getattr(item, name)] for name in self._feature_columns})
        prediction = self.model.predict(features)
        logger.info(
            "prediction",
            symbol=item.symbol,
            price=item.price,
            signal=prediction.signal.value,
            confidence=round(prediction.confidence, 4),
            threshold=self.confidence_threshold,
            metadata=prediction.metadata,
        )
        if prediction.confidence < self.confidence_threshold:
            logger.info(
                "signal_below_threshold",
                symbol=item.symbol,
                confidence=round(prediction.confidence, 4),
                threshold=self.confidence_threshold,
            )
            return None

        self._last_signal_time[item.symbol] = now
        return TradeSignal(
            symbol=item.symbol,
            signal=prediction.signal,
            confidence=prediction.confidence,
            price=item.price,
            target_exposure=prediction.target_exposure,
            realised_vol=item.realised_vol if prediction.target_exposure is not None else None,
        )
