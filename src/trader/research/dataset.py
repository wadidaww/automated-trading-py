"""Turn per-symbol bars into one aligned (features, label, next-bar return) panel."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trader.data.features import FeatureSpec, build_features
from trader.research.labels import LabelSpec, forward_return, make_labels, trailing_volatility


@dataclass(frozen=True, slots=True)
class Dataset:
    """Model-ready panel indexed by ``(timestamp, symbol)``.

    ``next_return`` is the close-to-close return of the following bar: the P&L of a position
    taken at the close of ``timestamp``, used to score the strategy. ``close`` and
    ``volatility`` (trailing log-return std over the label's ``vol_window``) are the inputs the
    Monte Carlo planner needs; they are not model features.
    """

    X: pd.DataFrame
    y: pd.Series
    next_return: pd.Series
    close: pd.Series
    volatility: pd.Series

    @property
    def dates(self) -> pd.DatetimeIndex:
        """Sorted unique timestamps in the panel."""
        return pd.DatetimeIndex(self.X.index.get_level_values("timestamp")).unique().sort_values()

    def rows_for(self, dates: pd.DatetimeIndex) -> pd.Series:
        """Boolean row mask for the given timestamps."""
        stamps = self.X.index.get_level_values("timestamp")
        return pd.Series(stamps.isin(dates), index=self.X.index)


def feature_panel(
    bars_by_symbol: dict[str, pd.DataFrame],
    features: FeatureSpec,
    vol_window: int = 20,
    rank_features: bool = False,
) -> pd.DataFrame:
    """Features for every bar of every symbol, indexed by ``(timestamp, symbol)``.

    Besides the feature columns it carries ``__close``, ``__volatility`` and ``__next_return``.
    Rows are not filtered, so the latest (still unlabelled) bars are included.
    """
    parts: list[pd.DataFrame] = []
    for symbol, bars in bars_by_symbol.items():
        frame = build_features(bars, features)
        close = bars["close"].astype(float)
        frame["__next_return"] = forward_return(close, 1)
        frame["__close"] = close
        frame["__volatility"] = trailing_volatility(close, vol_window)
        frame["symbol"] = symbol
        frame.index.name = "timestamp"
        parts.append(frame.set_index("symbol", append=True))
    panel = pd.concat(parts).sort_index()
    if "market" in features.groups:
        panel = panel.join(market_features(bars_by_symbol, features.windows))
    if rank_features:
        cols = feature_columns(panel)
        panel[cols] = panel.groupby(level="timestamp")[cols].rank(pct=True)
    return panel


def feature_columns(panel: pd.DataFrame) -> list[str]:
    """The model feature columns of a ``feature_panel``."""
    return [c for c in panel.columns if not c.startswith("__")]


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
    panel = feature_panel(bars_by_symbol, features, label.vol_window, rank_features)
    labels = pd.concat(
        {s: make_labels(b, label) for s, b in bars_by_symbol.items()}, names=["symbol"]
    ).swaplevel()
    labels.index = labels.index.set_names(["timestamp", "symbol"])
    panel["__label"] = labels
    feature_cols = feature_columns(panel)
    panel = panel.dropna(subset=[*feature_cols, "__label"])
    if panel.empty:
        raise ValueError("no rows with complete features and labels; is the history long enough?")
    return Dataset(
        X=panel[feature_cols],
        y=panel["__label"].rename("label"),
        next_return=panel["__next_return"].rename("next_return"),
        close=panel["__close"].rename("close"),
        volatility=panel["__volatility"].rename("volatility"),
    )


def market_features(
    bars_by_symbol: dict[str, pd.DataFrame], windows: tuple[int, ...]
) -> pd.DataFrame:
    """Returns relative to the equal-weight universe, and rolling beta to it.

    The "market" is the mean return of the symbols in ``bars_by_symbol`` on each date, so this
    group needs several symbols to be informative (with one symbol every relative return is 0).
    """
    closes = pd.DataFrame({s: b["close"].astype(float) for s, b in bars_by_symbol.items()})
    daily = closes.pct_change(fill_method=None)
    market_daily = daily.mean(axis=1)
    columns: dict[str, pd.DataFrame] = {}
    for w in windows:
        ret_w = closes.pct_change(w, fill_method=None)
        market_w = ret_w.mean(axis=1)
        columns[f"market_ret_{w}"] = pd.DataFrame(
            {s: market_w for s in closes.columns}, index=closes.index
        )
        columns[f"rel_ret_{w}"] = ret_w.sub(market_w, axis=0)
    beta_window = max(max(windows), 20)
    variance = market_daily.rolling(beta_window).var()
    columns[f"beta_{beta_window}"] = daily.apply(
        lambda col: col.rolling(beta_window).cov(market_daily) / variance.replace(0, np.nan)
    )
    stacked = {name: frame.stack(future_stack=True) for name, frame in columns.items()}
    out = pd.DataFrame(stacked)
    out.index = out.index.set_names(["timestamp", "symbol"])
    return out
