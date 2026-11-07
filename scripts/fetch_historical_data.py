"""Download historical bars from Futu OpenD into the CSV cache used by training and research.

Example: ``PYTHONPATH=src python scripts/fetch_historical_data.py --symbol HK.00700,HK.09988
--start 2015-01-01``. Needs a running OpenD; each new symbol uses history-kline quota.
"""

from __future__ import annotations

import argparse

from trader.data.fetcher import DEFAULT_CACHE_DIR, HistoricalDataFetcher


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="HK.00700", help="comma-separated Futu codes")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD")
    parser.add_argument("--ktype", default="K_DAY", help="Futu KLType, e.g. K_DAY or K_1M")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    args = parser.parse_args()
    fetcher = HistoricalDataFetcher(
        host=args.host, port=args.port, cache_dir=args.cache_dir, ktype=args.ktype
    )
    for symbol in [s.strip() for s in args.symbol.split(",") if s.strip()]:
        bars = fetcher.fetch_and_cache(symbol, args.start, args.end)
        print(f"{symbol}: {len(bars)} bars, {bars.index[0]} to {bars.index[-1]}")


if __name__ == "__main__":
    main()
