"""Shared feature DataFrame builders for model tests."""

from __future__ import annotations

import pandas as pd


def model_features(rows: int = 8) -> pd.DataFrame:
    """Build a deterministic feature frame for model training/prediction."""
    return pd.DataFrame(
        {
            "close": [100.0 + index for index in range(rows)],
            "volume": [1_000.0 + index * 10.0 for index in range(rows)],
            "return_1d": [index / 100.0 for index in range(rows)],
        }
    )
