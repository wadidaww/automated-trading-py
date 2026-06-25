"""Fetch historical data for configured symbols."""

from __future__ import annotations

import argparse

from futu_trader.data.fetcher import HistoricalDataFetcher


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="700.HK")
    args = parser.parse_args()
    fetcher = HistoricalDataFetcher()
    data = fetcher.fetch(args.symbol)
    print(data.tail(3))


if __name__ == "__main__":
    main()
