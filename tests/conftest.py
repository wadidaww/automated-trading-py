"""Pytest fixtures."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def ohlcv_df() -> pd.DataFrame:
    """Provide deterministic OHLCV fixture."""
    return pd.DataFrame(
        {
            "close": [100 + i for i in range(40)],
            "volume": [1000 + i for i in range(40)],
        }
    )
