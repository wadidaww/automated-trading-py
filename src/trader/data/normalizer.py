"""Feature engineering utilities."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _rsi(delta: pd.Series) -> pd.Series:
    """Relative strength index over a 14-bar window."""
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean().replace(0, np.nan)
    return 100 - (100 / (1 + gain / loss))


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """MACD line and its signal line."""
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    return macd, macd.ewm(span=9, adjust=False).mean()


def _bollinger_bands(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Upper and lower bands two deviations from the 20-bar mean."""
    mid = close.rolling(20).mean()
    band = 2 * close.rolling(20).std()
    return mid + band, mid - band


def _z_score(close: pd.Series) -> pd.Series:
    """Distance from the 20-bar mean in standard deviations."""
    z_mean = close.rolling(20).mean()
    z_std = close.rolling(20).std().replace(0, np.nan)
    return (close - z_mean) / z_std


class DataNormalizer:
    """Stateless feature engineering methods."""

    @staticmethod
    def add_features(df: pd.DataFrame) -> pd.DataFrame:
        """Add trading features to OHLCV frame.

        Args:
            df: Input OHLCV dataframe.

        Returns:
            pd.DataFrame: Feature-enhanced frame.
        """
        out = df.copy()
        close = out["close"].astype(float)
        volume = out["volume"].astype(float)
        delta = close.diff()

        out["rsi_14"] = _rsi(delta)
        out["macd"], out["macd_signal"] = _macd(close)
        out["bb_upper"], out["bb_lower"] = _bollinger_bands(close)
        out["vwap"] = (close * volume).cumsum() / volume.cumsum().replace(0, np.nan)
        out["obv"] = (np.sign(delta.fillna(0)) * volume).cumsum()
        out["log_return"] = np.log(close / close.shift(1)).fillna(0.0)
        out["realized_volatility"] = out["log_return"].rolling(20).std() * np.sqrt(252)
        out["z_score"] = _z_score(close)
        for lag in (1, 2, 5, 10):
            out[f"close_lag_{lag}"] = close.shift(lag)

        return out
