"""Mean reversion model with adaptive thresholds and momentum confirmation."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from trader.model.base import ISignalModel, Prediction, Signal

_REFERENCE_VOLATILITY = 0.02
_MAX_VOLATILITY_SCALAR = 1.5
_CONFIDENCE_Z_SCALE = 3.0
_MAX_CONFIDENCE = 1.0
_BASELINE_RSI = 50.0
_BASELINE_BB_POSITION = 0.5
_RSI_BOOST_FACTOR = 1.2
_BB_BOOST_FACTOR = 1.15

_MomentumCheck = Callable[[float, float], bool]


@dataclass(frozen=True, slots=True)
class _Threshold:
    """A bound together with the side of the comparison it accepts."""

    bound: float
    below: bool

    def matches(self, value: float) -> bool:
        """Return True when value sits on the configured side of the bound."""
        return value < self.bound if self.below else value > self.bound


@dataclass(frozen=True, slots=True)
class _SideRule:
    """Confirmation and confidence-boost rules for one trade side."""

    momentum: _MomentumCheck
    rsi_confirm: _Threshold
    trend_confirm: _Threshold
    rsi_boost: _Threshold
    bb_boost: _Threshold


_SIDE_RULES: dict[Signal, _SideRule] = {
    Signal.BUY: _SideRule(
        momentum=lambda momentum, acceleration: momentum < 0 or acceleration > 0,
        rsi_confirm=_Threshold(bound=30.0, below=False),
        trend_confirm=_Threshold(bound=0.005, below=True),
        rsi_boost=_Threshold(bound=40.0, below=True),
        bb_boost=_Threshold(bound=0.2, below=True),
    ),
    Signal.SELL: _SideRule(
        momentum=lambda momentum, acceleration: momentum > 0 or acceleration < 0,
        rsi_confirm=_Threshold(bound=70.0, below=True),
        trend_confirm=_Threshold(bound=-0.005, below=False),
        rsi_boost=_Threshold(bound=60.0, below=False),
        bb_boost=_Threshold(bound=0.8, below=False),
    ),
}


@dataclass(frozen=True, slots=True)
class _FeatureWindow:
    """Latest confirmation-feature values; neutral defaults in legacy mode."""

    momentum: float
    trend_strength: float
    volatility: float
    rsi: float
    bb_position: float
    acceleration: float
    enhanced: bool


_LEGACY_FEATURES = _FeatureWindow(
    momentum=0.0,
    trend_strength=0.0,
    volatility=0.0,
    rsi=_BASELINE_RSI,
    bb_position=_BASELINE_BB_POSITION,
    acceleration=0.0,
    enhanced=False,
)


def _read_features(features: pd.DataFrame) -> _FeatureWindow:
    """Read the latest confirmation features, or fall back to legacy defaults."""
    if "momentum" not in features.columns:
        return _LEGACY_FEATURES
    return _FeatureWindow(
        momentum=float(features["momentum"].iloc[-1]),
        trend_strength=float(features["trend_strength"].iloc[-1]),
        volatility=float(features["volatility"].iloc[-1]),
        rsi=float(features["rsi"].iloc[-1]),
        bb_position=float(features["bb_position"].iloc[-1]),
        acceleration=float(features["price_acceleration"].iloc[-1]),
        enhanced=True,
    )


def _apply_boost(confidence: float, value: float, threshold: _Threshold, factor: float) -> float:
    """Raise confidence by factor when value crosses the boost threshold."""
    if not threshold.matches(value):
        return confidence
    return min(confidence * factor, _MAX_CONFIDENCE)


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
        window = _read_features(features)
        buy_threshold, sell_threshold = self._adjusted_thresholds(window.volatility)

        side = self._classify(z_score, buy_threshold, sell_threshold)
        signal = Signal.HOLD
        confidence = 0.0
        if side is not Signal.HOLD and self._confirms(side, window):
            signal = side
            confidence = self._confidence(side, z_score, window)

        return Prediction(
            signal=signal,
            confidence=confidence,
            metadata={
                "z_score": z_score,
                "momentum": window.momentum,
                "trend_strength": window.trend_strength,
                "volatility": window.volatility,
                "rsi": window.rsi,
                "bb_position": window.bb_position,
                "acceleration": window.acceleration,
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

    def _adjusted_thresholds(self, volatility: float) -> tuple[float, float]:
        """Widen thresholds in calm markets when volatility adjustment is on."""
        buy_threshold = self.buy_threshold
        sell_threshold = self.sell_threshold
        if self.volatility_adjust and volatility > 0:
            scalar = min(volatility / _REFERENCE_VOLATILITY, _MAX_VOLATILITY_SCALAR)
            buy_threshold *= scalar
            sell_threshold *= scalar
        return buy_threshold, sell_threshold

    @staticmethod
    def _classify(z_score: float, buy_threshold: float, sell_threshold: float) -> Signal:
        """Map a z-score onto the trade side its thresholds indicate."""
        if z_score < buy_threshold:
            return Signal.BUY
        if z_score > sell_threshold:
            return Signal.SELL
        return Signal.HOLD

    def _confirms(self, side: Signal, window: _FeatureWindow) -> bool:
        """Check momentum, RSI, and trend confirmation for a candidate side."""
        if not window.enhanced:
            return True
        rule = _SIDE_RULES[side]
        momentum_ok = not self.use_momentum or rule.momentum(window.momentum, window.acceleration)
        rsi_ok = rule.rsi_confirm.matches(window.rsi)
        trend_ok = rule.trend_confirm.matches(window.trend_strength)
        return momentum_ok and rsi_ok and trend_ok

    @staticmethod
    def _confidence(side: Signal, z_score: float, window: _FeatureWindow) -> float:
        """Score confidence from z-score depth, then apply feature boosts."""
        confidence = min(abs(z_score) / _CONFIDENCE_Z_SCALE, _MAX_CONFIDENCE)
        if not window.enhanced:
            return confidence
        rule = _SIDE_RULES[side]
        confidence = _apply_boost(confidence, window.rsi, rule.rsi_boost, _RSI_BOOST_FACTOR)
        return _apply_boost(confidence, window.bb_position, rule.bb_boost, _BB_BOOST_FACTOR)
