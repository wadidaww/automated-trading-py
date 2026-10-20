from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from trader.data.fetcher import HistoricalDataFetcher, cache_path, load_history


def _page(days: list[str], close: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": "HK.00700",
            "time_key": [f"{d} 00:00:00" for d in days],
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1000,
            "turnover": 1000 * close,
        }
    )


class FakeKlineContext:
    def __init__(self, responses: list[tuple[int, Any, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request_history_kline(self, code: str, **kwargs: Any) -> tuple[int, Any, Any]:
        self.calls.append({"code": code, **kwargs})
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def _fetcher(context: FakeKlineContext, tmp_path: Any) -> HistoricalDataFetcher:
    return HistoricalDataFetcher(cache_dir=str(tmp_path), context_factory=lambda: context)


def test_fetch_follows_pages_and_normalises_symbol(tmp_path) -> None:
    context = FakeKlineContext(
        [
            (0, _page(["2024-01-02", "2024-01-03"], 10.0), "next"),
            (0, _page(["2024-01-04"], 11.0), None),
        ]
    )
    bars = _fetcher(context, tmp_path).fetch("700.HK", start="2024-01-01")
    assert list(bars["close"]) == [10.0, 10.0, 11.0]
    assert bars.index.name == "timestamp"
    assert [c["page_req_key"] for c in context.calls] == [None, "next"]
    assert context.calls[0]["code"] == "HK.00700"
    assert context.calls[0]["ktype"] == "K_DAY"
    assert context.closed


def test_fetch_raises_on_error_and_on_empty(tmp_path) -> None:
    failing = FakeKlineContext([(-1, "quota exceeded", None)])
    with pytest.raises(RuntimeError, match="quota exceeded"):
        _fetcher(failing, tmp_path).fetch("HK.00700")
    assert failing.closed
    empty = FakeKlineContext([(0, pd.DataFrame(), None)])
    with pytest.raises(RuntimeError, match="no bars"):
        _fetcher(empty, tmp_path).fetch("HK.00700")


def test_cache_round_trip_merges_new_rows(tmp_path) -> None:
    context = FakeKlineContext(
        [
            (0, _page(["2024-01-02", "2024-01-03"], 10.0), None),
            (0, _page(["2024-01-03", "2024-01-04"], 12.0), None),
        ]
    )
    fetcher = _fetcher(context, tmp_path)
    fetcher.fetch_and_cache("HK.00700")
    fetcher.fetch_and_cache("HK.00700")
    bars = load_history("HK.700", cache_dir=str(tmp_path))
    assert list(bars["close"]) == [10.0, 12.0, 12.0]
    assert cache_path("700.HK", cache_dir=str(tmp_path)).name == "HK.00700_K_DAY.csv"


def test_load_history_missing_cache_says_how_to_fetch(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="fetch_historical_data"):
        load_history("HK.00700", cache_dir=str(tmp_path))
