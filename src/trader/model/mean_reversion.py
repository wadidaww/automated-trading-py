"""Mean reversion model with adaptive thresholds and momentum confirmation."""

from __future__ import annotations

import json

import pandas as pd

from trader.model.base import ISignalModel, Prediction, Signal


class MeanReversionModel(ISignalModel):
    """Enhanced z-score threshold model with multi-feature confirmation.

    Uses z-score as the primary signal but confirms with momentum,
    trend strength, RSI, and Bollinger Band position to filter
    false signals and improve win rate.
    """

    def __init__(
        self,
        buy_threshold: float = -2.0,
        sell_threshold: float = 2.0,
        use_momentum: bool = True,
        volatility_adjust: bool = True,
    ) -> None:
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.use_momentum = use_momentum
        self.volatility_adjust = volatility_adjust

    def predict(self, features: pd.DataFrame) -> Prediction:
        """Predict using z-score with multi-feature confirmation.

        Signal logic:
        - BUY when z-score is below threshold AND momentum confirms
          (price decelerating or reversing) AND RSI not oversold
        - SELL when z-score is above threshold AND momentum confirms
          (price accelerating upward or reversing) AND RSI not overbought
        - HOLD otherwise

        When only z_score is provided (legacy mode), uses z-score only
        without multi-feature confirmation for backward compatibility.
        """
        z_score = float(features["z_score"].iloc[-1])
        has_enhanced_features = "momentum" in features.columns

        momentum = 0.0
        trend = 0.0
        volatility = 0.0
        rsi = 50.0
        bb_pos = 0.5
        accel = 0.0

        if has_enhanced_features:
            momentum = float(features["momentum"].iloc[-1])
            trend = float(features["trend_strength"].iloc[-1])
            volatility = float(features["volatility"].iloc[-1])
            rsi = float(features["rsi"].iloc[-1])
            bb_pos = float(features["bb_position"].iloc[-1])
            accel = float(features["price_acceleration"].iloc[-1])

        buy_threshold = self.buy_threshold
        sell_threshold = self.sell_threshold

        if self.volatility_adjust and volatility > 0:
            vol_scalar = min(volatility / 0.02, 1.5)
            buy_threshold *= vol_scalar
            sell_threshold *= vol_scalar

        signal = Signal.HOLD
        confidence = 0.0

        if z_score < buy_threshold:
            if has_enhanced_features:
                momentum_ok = (not self.use_momentum) or (momentum < 0 or accel > 0)
                rsi_ok = rsi > 30
                trend_ok = trend < 0.005
                confirm = momentum_ok and rsi_ok and trend_ok
            else:
                confirm = True

            if confirm:
                signal = Signal.BUY
                confidence = min(abs(z_score) / 3.0, 1.0)
                if has_enhanced_features:
                    if rsi < 40:
                        confidence = min(confidence * 1.2, 1.0)
                    if bb_pos < 0.2:
                        confidence = min(confidence * 1.15, 1.0)
        elif z_score > sell_threshold:
            if has_enhanced_features:
                momentum_ok = (not self.use_momentum) or (momentum > 0 or accel < 0)
                rsi_ok = rsi < 70
                trend_ok = trend > -0.005
                confirm = momentum_ok and rsi_ok and trend_ok
            else:
                confirm = True

            if confirm:
                signal = Signal.SELL
                confidence = min(abs(z_score) / 3.0, 1.0)
                if has_enhanced_features:
                    if rsi > 60:
                        confidence = min(confidence * 1.2, 1.0)
                    if bb_pos > 0.8:
                        confidence = min(confidence * 1.15, 1.0)

        return Prediction(
            signal=signal,
            confidence=confidence,
            metadata={
                "z_score": z_score,
                "momentum": momentum,
                "trend_strength": trend,
                "volatility": volatility,
                "rsi": rsi,
                "bb_position": bb_pos,
                "acceleration": accel,
                "adjusted_buy_threshold": buy_threshold,
                "adjusted_sell_threshold": sell_threshold,
            },
        )

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """No-op for rule model."""

    def save(self, path: str) -> None:
        """Save config to JSON."""
        payload = {
            "buy_threshold": self.buy_threshold,
            "sell_threshold": self.sell_threshold,
            "use_momentum": self.use_momentum,
            "volatility_adjust": self.volatility_adjust,
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    @classmethod
    def load(cls, path: str) -> MeanReversionModel:
        """Load model config from JSON."""
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls(**payload)
