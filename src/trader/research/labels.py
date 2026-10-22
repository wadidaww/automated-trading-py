"""Training labels. Each label at bar ``t`` looks ``horizon`` bars ahead; the last rows are NaN.

Features at ``t`` may only use bars ``<= t``; labels use bars ``t+1 .. t+horizon``. Validation must
therefore purge ``horizon`` bars between training and test data (see ``validation``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
import pandas as pd

LabelKind = Literal["forward_return", "triple_barrier"]
_VOL_FLOOR: Final = 1e-8


@dataclass(frozen=True, slots=True)
class LabelSpec:
    """How to label each bar.

    ``forward_return`` is a regression target: the return over ``horizon`` bars, divided by the
    trailing volatility scaled to the horizon when ``vol_scale`` is set. ``triple_barrier`` is a
    classification target in {-1, 0, 1}: which of the profit-take, stop-loss or time barrier is
    touched first, with barriers ``profit_take``/``stop_loss`` trailing-volatility multiples away.
    """

    kind: LabelKind = "forward_return"
    horizon: int = 5
    vol_scale: bool = True
    vol_window: int = 20
    profit_take: float = 2.0
    stop_loss: float = 2.0

    @property
    def task(self) -> Literal["regression", "classification"]:
        """The model task this label calls for."""
        return "regression" if self.kind == "forward_return" else "classification"

    @property
    def key(self) -> str:
        """Stable short identifier used to cache and log label sets."""
        if self.kind == "forward_return":
            return f"fwd|h{self.horizon}|vs{int(self.vol_scale)}|vw{self.vol_window}"
        return f"tb|h{self.horizon}|pt{self.profit_take}|sl{self.stop_loss}|vw{self.vol_window}"


def trailing_volatility(close: pd.Series, window: int) -> pd.Series:
    """Rolling standard deviation of log returns, known at the close of each bar."""
    return np.log(close.astype(float)).diff().rolling(window).std()


def forward_return(close: pd.Series, horizon: int) -> pd.Series:
    """Simple return from the close of ``t`` to the close of ``t + horizon``."""
    return close.shift(-horizon) / close - 1


def make_labels(bars: pd.DataFrame, spec: LabelSpec) -> pd.Series:
    """Label every bar of one symbol; rows without a full horizon (or volatility) are NaN.

    Raises:
        ValueError: On a non-positive horizon or an unknown label kind.
    """
    if spec.horizon < 1:
        raise ValueError(f"label horizon must be >= 1, got {spec.horizon}")
    close = bars["close"].astype(float)
    vol = trailing_volatility(close, spec.vol_window)
    if spec.kind == "forward_return":
        target = forward_return(close, spec.horizon)
        if spec.vol_scale:
            target = target / (vol * np.sqrt(spec.horizon)).clip(lower=_VOL_FLOOR)
        return target.rename("label")
    if spec.kind == "triple_barrier":
        return _triple_barrier(bars, vol, spec).rename("label")
    raise ValueError(f"unknown label kind: {spec.kind!r}")


def _triple_barrier(bars: pd.DataFrame, vol: pd.Series, spec: LabelSpec) -> pd.Series:
    """First barrier touched within ``horizon`` bars: 1 upper, -1 lower, 0 neither.

    Uses bar highs and lows when present. When both barriers fall inside the same bar the order
    is unknown, so the stop-loss is assumed first (conservative).
    """
    close = bars["close"].to_numpy(dtype=float)
    high = bars["high"].to_numpy(dtype=float) if "high" in bars.columns else close
    low = bars["low"].to_numpy(dtype=float) if "low" in bars.columns else close
    sigma = vol.to_numpy(dtype=float)
    labels = np.full(len(close), np.nan)
    for t in range(len(close) - spec.horizon):
        if not np.isfinite(sigma[t]) or sigma[t] <= 0:
            continue
        upper = close[t] * (1 + spec.profit_take * sigma[t])
        lower = close[t] * (1 - spec.stop_loss * sigma[t])
        labels[t] = 0.0
        for step in range(t + 1, t + spec.horizon + 1):
            if low[step] <= lower:
                labels[t] = -1.0
                break
            if high[step] >= upper:
                labels[t] = 1.0
                break
    return pd.Series(labels, index=bars.index)
