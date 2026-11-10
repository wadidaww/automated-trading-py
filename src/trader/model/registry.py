"""Signal model registry keyed by ``model.type`` from config.

Register a builder to add a model type; the composition root (``trader.app.bootstrap``) only
calls :func:`build_model`. Unknown types fall back to ``MeanReversionModel``.

ML types load a fitted artifact from ``model.path`` and fail at startup when it is missing or
needs features the live pipeline does not compute, rather than on every tick.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

from trader.config import TREND_MODEL_TYPE, ModelSettings
from trader.data.features import LIVE_FEATURES
from trader.model.base import ISignalModel
from trader.model.ensemble import EnsembleSignalModel
from trader.model.mean_reversion import MeanReversionModel
from trader.model.tabular import ESTIMATORS as TABULAR_ESTIMATORS
from trader.model.trend import TrendFollowingModel

type ModelBuilder = Callable[[ModelSettings | None], ISignalModel]

DEFAULT_MODEL_TYPE = "mean_reversion"
# (name, weight, MeanReversionModel kwargs) for the "ensemble" model type.
ENSEMBLE_MEMBERS: tuple[tuple[str, float, dict[str, Any]], ...] = (
    ("standard", 0.4, {"buy_threshold": -2.0, "sell_threshold": 2.0}),
    ("aggressive", 0.3, {"buy_threshold": -1.5, "sell_threshold": 1.5}),
    (
        "conservative",
        0.3,
        {"buy_threshold": -2.5, "sell_threshold": 2.5, "volatility_adjust": True},
    ),
)


def _model_path(model_type: str, settings: ModelSettings | None) -> Path:
    """Fitted artifact for an ML model type; fails at startup, not on every tick."""
    if settings is None:
        raise ValueError(f"model type {model_type!r} needs a config with model.path")
    path = Path(settings.path)
    if not path.is_file():
        raise FileNotFoundError(
            f"model type {model_type!r} needs a fitted artifact at {path}; train one with "
            "trader.model.trainer or trader.research.grid_search"
        )
    return path


def _mean_reversion(_settings: ModelSettings | None) -> ISignalModel:
    return MeanReversionModel()


def _ensemble(_settings: ModelSettings | None) -> ISignalModel:
    """The weighted ensemble of mean reversion variants in ``ENSEMBLE_MEMBERS``."""
    ensemble = EnsembleSignalModel()
    for name, weight, params in ENSEMBLE_MEMBERS:
        ensemble.add_model(MeanReversionModel(**params), weight=weight, name=name)
    return ensemble


def _trend(_settings: ModelSettings | None) -> ISignalModel:
    """Rule-based trend model; its lookbacks live in ``model.trend`` and feed ``DataStage``."""
    return TrendFollowingModel()


def _gradient_boosting(settings: ModelSettings | None) -> ISignalModel:
    """Load the fitted sklearn gradient boosting classifier from ``model.path``."""
    from trader.model.gradient_boosting import GradientBoostingModel

    return GradientBoostingModel.load(str(_model_path("gradient_boosting", settings)))


def _tabular(model_type: str, settings: ModelSettings | None) -> ISignalModel:
    """Load a fitted ``TabularModel`` and check it only needs live ``DataStage`` features."""
    from trader.model.tabular import TabularModel

    model = TabularModel.load(str(_model_path(model_type, settings)))
    missing = sorted(set(model.feature_names) - set(LIVE_FEATURES))
    if missing:
        raise ValueError(
            f"model at {settings.path if settings else ''} uses features the "
            f"live pipeline does not compute: {missing}; train it on the 'live' feature group"
        )
    if model.estimator != model_type and model_type != "tabular":
        raise ValueError(
            f"model.type is {model_type!r} but the artifact is a {model.estimator!r} model"
        )
    return model


_BUILDERS: dict[str, ModelBuilder] = {
    DEFAULT_MODEL_TYPE: _mean_reversion,
    "ensemble": _ensemble,
    TREND_MODEL_TYPE: _trend,
    "gradient_boosting": _gradient_boosting,
    **{name: functools.partial(_tabular, name) for name in (*TABULAR_ESTIMATORS, "tabular")},
}


def register_model(model_type: str, builder: ModelBuilder) -> None:
    """Register (or replace) the builder for ``model_type``."""
    _BUILDERS[model_type] = builder


def registered_models() -> tuple[str, ...]:
    """Every model type with a registered builder."""
    return tuple(_BUILDERS)


def build_model(model_type: str, settings: ModelSettings | None = None) -> ISignalModel:
    """Build the signal model for ``model_type``; unknown types get ``MeanReversionModel``."""
    return _BUILDERS.get(model_type, _mean_reversion)(settings)
