"""Historical bar loader: Futu OpenD ``request_history_kline`` with a CSV cache.

``HistoricalDataFetcher.fetch`` pages through OpenD; ``save``/``load_history`` keep one CSV per
symbol and bar type under the cache directory, so research and training run offline once the
history is downloaded. Each download consumes Futu's history-kline quota (unique symbols per 30
days), so download once and reuse the cache.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, Protocol

import pandas as pd

from trader.core.symbols import normalize_symbol
from trader.utils.logger import get_logger

logger = get_logger("fetcher")

BAR_COLUMNS: Final[tuple[str, ...]] = ("open", "high", "low", "close", "volume", "turnover")
DEFAULT_CACHE_DIR: Final = "data/raw"
_PAGE_SIZE: Final = 1000
_RET_OK: Final = 0


class QuoteContext(Protocol):
    """The part of ``futu.OpenQuoteContext`` the fetcher uses."""

    def request_history_kline(self, code: str, **kwargs: Any) -> tuple[int, Any, Any]:
        """Return ``(ret, frame_or_error, next_page_key)``."""

    def close(self) -> None:
        """Release the connection."""


def _default_context_factory(host: str, port: int) -> Callable[[], QuoteContext]:
    def build() -> QuoteContext:
        from futu import OpenQuoteContext

        context: QuoteContext = OpenQuoteContext(host=host, port=port)
        return context

    return build


def cache_path(symbol: str, ktype: str = "K_DAY", cache_dir: str = DEFAULT_CACHE_DIR) -> Path:
    """CSV path for one symbol and bar type, e.g. ``data/raw/HK.00700_K_DAY.csv``."""
    return Path(cache_dir) / f"{normalize_symbol(symbol)}_{ktype}.csv"


def load_history(
    symbol: str, ktype: str = "K_DAY", cache_dir: str = DEFAULT_CACHE_DIR
) -> pd.DataFrame:
    """Load cached bars, indexed by bar time, oldest first.

    Raises:
        FileNotFoundError: When the symbol has not been downloaded yet.
    """
    path = cache_path(symbol, ktype, cache_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"no cached history at {path}; run scripts/fetch_historical_data.py --symbol {symbol}"
        )
    bars = pd.read_csv(path, index_col="timestamp", parse_dates=["timestamp"])
    return bars.sort_index()


class HistoricalDataFetcher:
    """Download historical bars from OpenD and cache them as CSV."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        cache_dir: str = DEFAULT_CACHE_DIR,
        ktype: str = "K_DAY",
        autype: str = "qfq",
        context_factory: Callable[[], QuoteContext] | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.ktype = ktype
        self.autype = autype
        self._context_factory = context_factory or _default_context_factory(host, port)

    def fetch(self, symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """Download bars for ``symbol`` between ``start`` and ``end`` (``YYYY-MM-DD``).

        Returns:
            Bars indexed by ``timestamp`` with ``BAR_COLUMNS``, oldest first.

        Raises:
            RuntimeError: When OpenD returns an error or no bars.
        """
        code = normalize_symbol(symbol)
        context = self._context_factory()
        pages: list[pd.DataFrame] = []
        try:
            page_key: Any = None
            while True:
                ret, data, page_key = context.request_history_kline(
                    code,
                    start=start,
                    end=end,
                    ktype=self.ktype,
                    autype=self.autype,
                    max_count=_PAGE_SIZE,
                    page_req_key=page_key,
                )
                if ret != _RET_OK:
                    raise RuntimeError(f"request_history_kline failed for {code}: {data}")
                pages.append(data)
                if page_key is None:
                    break
        finally:
            context.close()
        bars = _to_bars(pd.concat(pages, ignore_index=True))
        if bars.empty:
            raise RuntimeError(f"OpenD returned no bars for {code} ({start} to {end})")
        logger.info("history_fetched", symbol=code, ktype=self.ktype, bars=len(bars))
        return bars

    def save(self, symbol: str, bars: pd.DataFrame) -> Path:
        """Write bars to the cache, merging with any rows already there (new rows win)."""
        path = cache_path(symbol, self.ktype, self.cache_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            cached = load_history(symbol, self.ktype, self.cache_dir)
            bars = pd.concat([cached, bars])
            bars = bars[~bars.index.duplicated(keep="last")].sort_index()
        bars.to_csv(path, index_label="timestamp")
        return path

    def fetch_and_cache(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame:
        """Download bars and merge them into the cache."""
        bars = self.fetch(symbol, start, end)
        path = self.save(symbol, bars)
        logger.info("history_cached", symbol=normalize_symbol(symbol), path=str(path))
        return bars


def _to_bars(raw: pd.DataFrame) -> pd.DataFrame:
    """Map a Futu kline frame (``time_key`` + OHLCV columns) to the cache layout."""
    if raw.empty:
        return pd.DataFrame(columns=list(BAR_COLUMNS))
    columns = [c for c in BAR_COLUMNS if c in raw.columns]
    bars = raw[columns].astype(float)
    bars.index = pd.DatetimeIndex(pd.to_datetime(raw["time_key"]), name="timestamp")
    return bars[~bars.index.duplicated(keep="last")].sort_index()
