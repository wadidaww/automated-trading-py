"""Feature engineering utilities."""

from __future__ import annotations

import numpy as np
import pandas as pd


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
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = -delta.clip(upper=0).rolling(14).mean().replace(0, np.nan)
        rs = gain / loss
        out["rsi_14"] = 100 - (100 / (1 + rs))

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        out["macd"] = ema12 - ema26
        out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()

        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        out["bb_upper"] = mid + 2 * std
        out["bb_lower"] = mid - 2 * std

        out["vwap"] = (close * volume).cumsum() / volume.cumsum().replace(0, np.nan)
        out["obv"] = (np.sign(delta.fillna(0)) * volume).cumsum()
        out["log_return"] = np.log(close / close.shift(1)).fillna(0.0)
        out["realized_volatility"] = out["log_return"].rolling(20).std() * np.sqrt(252)

        z_mean = close.rolling(20).mean()
        z_std = close.rolling(20).std().replace(0, np.nan)
        out["z_score"] = (close - z_mean) / z_std

        for lag in (1, 2, 5, 10):
            out[f"close_lag_{lag}"] = close.shift(lag)

        return out
