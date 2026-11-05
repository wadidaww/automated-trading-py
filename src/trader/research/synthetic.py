"""Synthetic OHLCV bars for tests and for trying the research tools without OpenD.

The returns carry a small, known amount of short-term momentum, so a working pipeline should
find a positive but modest information coefficient. Numbers from synthetic data say nothing about
real markets.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def synthetic_bars(
    n_bars: int = 750,
    seed: int = 0,
    autocorrelation: float = 0.1,
    daily_vol: float = 0.015,
    start: str = "2020-01-01",
) -> pd.DataFrame:
    """Business-day OHLCV bars from an AR(1) return process.

    Args:
        n_bars: Number of bars.
        seed: Random seed.
        autocorrelation: AR(1) coefficient of daily returns (the planted edge).
        daily_vol: Standard deviation of the return shocks.
        start: First bar date.
    """
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0.0, daily_vol, n_bars)
    returns = np.empty(n_bars)
    returns[0] = shocks[0]
    for t in range(1, n_bars):
        returns[t] = autocorrelation * returns[t - 1] + shocks[t]
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[100.0], close[:-1]]) * np.exp(rng.normal(0, daily_vol / 4, n_bars))
    wick = np.abs(rng.normal(0, daily_vol / 2, n_bars))
    high = np.maximum(open_, close) * (1 + wick)
    low = np.minimum(open_, close) * (1 - wick)
    volume = rng.lognormal(mean=13.0, sigma=0.3, size=n_bars)
    index = pd.bdate_range(start=start, periods=n_bars, name="timestamp")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )
