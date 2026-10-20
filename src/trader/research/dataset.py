"""Turn per-symbol bars into one aligned (features, label, next-bar return) panel."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trader.data.features import FeatureSpec, build_features
from trader.research.labels import LabelSpec, forward_return, make_labels


@dataclass(frozen=True, slots=True)
class Dataset:
    """Model-ready panel indexed by ``(timestamp, symbol)``.

    ``next_return`` is the close-to-close return of the following bar: the P&L of a position
    taken at the close of ``timestamp``, used to score the strategy.
    """

    X: pd.DataFrame
    y: pd.Series
    next_return: pd.Series

    @property
    def dates(self) -> pd.DatetimeIndex:
        """Sorted unique timestamps in the panel."""
        return pd.DatetimeIndex(self.X.index.get_level_values("timestamp")).unique().sort_values()

    def rows_for(self, dates: pd.DatetimeIndex) -> pd.Series:
        """Boolean row mask for the given timestamps."""
        stamps = self.X.index.get_level_values("timestamp")
        return pd.Series(stamps.isin(dates), index=self.X.index)


def build_dataset(
    bars_by_symbol: dict[str, pd.DataFrame],
    features: FeatureSpec,
    label: LabelSpec,
    rank_features: bool = False,
) -> Dataset:
    """Build features and labels per symbol and stack them into one panel.

    Args:
        bars_by_symbol: OHLCV bars per symbol, indexed by timestamp.
        features: Feature groups and windows.
        label: Label definition.
        rank_features: Replace each feature with its cross-sectional percentile rank per date
            (useful with several symbols; removes level differences between them).

    Returns:
        Rows with complete features and a label; warm-up and end-of-history rows are dropped.

    Raises:
        ValueError: When no symbol yields a usable row.
    """
    parts: list[pd.DataFrame] = []
    for symbol, bars in bars_by_symbol.items():
        frame = build_features(bars, features)
        frame["__label"] = make_labels(bars, label)
        frame["__next_return"] = forward_return(bars["close"].astype(float), 1)
        frame["symbol"] = symbol
        frame.index.name = "timestamp"
        parts.append(frame.set_index("symbol", append=True))
    panel = pd.concat(parts).sort_index()
    feature_cols = [c for c in panel.columns if not c.startswith("__")]
    if rank_features:
        panel[feature_cols] = panel.groupby(level="timestamp")[feature_cols].rank(pct=True)
    panel = panel.dropna(subset=[*feature_cols, "__label"])
    if panel.empty:
        raise ValueError("no rows with complete features and labels; is the history long enough?")
    return Dataset(
        X=panel[feature_cols],
        y=panel["__label"].rename("label"),
        next_return=panel["__next_return"].rename("next_return"),
    )
