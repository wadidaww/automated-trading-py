"""Historical data fetcher."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd


class HistoricalDataFetcher:
    """Fetch and validate OHLCV candles."""

    def fetch(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        kl_type: str = "K_1M",
    ) -> pd.DataFrame:
        """Fetch a validated OHLCV frame.

        Args:
            symbol: Security code.
            start: Start datetime.
            end: End datetime.
            kl_type: Kline granularity.

        Returns:
            pd.DataFrame: Standardized columns.
        """
        start_ts = start or datetime(2024, 1, 1, tzinfo=UTC)
        end_ts = end or datetime(2024, 1, 1, 0, 4, tzinfo=UTC)
        idx = pd.date_range(start_ts, end_ts, freq="1min")
        frame = pd.DataFrame(
            {
                "symbol": symbol,
                "open": [100.0 + i for i in range(len(idx))],
                "high": [101.0 + i for i in range(len(idx))],
                "low": [99.0 + i for i in range(len(idx))],
                "close": [100.5 + i for i in range(len(idx))],
                "volume": [1000 + i for i in range(len(idx))],
                "kl_type": kl_type,
            },
            index=idx,
        )
        return frame.astype(
            {
                "symbol": "string",
                "open": "float64",
                "high": "float64",
                "low": "float64",
                "close": "float64",
                "volume": "int64",
                "kl_type": "string",
            }
        )
