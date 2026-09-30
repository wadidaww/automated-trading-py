"""Run a backtest over recorded trades and print metrics.

Fails (non-zero exit) when there is nothing to backtest instead of printing placeholder numbers.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from trader.evaluation.backtester import Backtester
from trader.evaluation.metrics import Trade
from trader.utils.config import load_config


def _load_trades(path: Path) -> list[Trade]:
    """Read trades from a CSV with ``pnl_minor`` and ``notional_minor`` columns."""
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            Trade(pnl_minor=int(row["pnl_minor"]), notional_minor=int(row["notional_minor"]))
            for row in csv.DictReader(handle)
        ]


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Run a backtest")
    parser.add_argument("--config", default="config/config.dev.yaml")
    parser.add_argument("--trades", type=Path, help="CSV of trades (pnl_minor, notional_minor)")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.trades is None or not args.trades.exists():
        raise SystemExit(f"no trades to backtest for {config.trading.symbols}: pass --trades <csv>")
    trades = _load_trades(args.trades)
    if not trades:
        raise SystemExit(f"{args.trades} contains no trades")
    print(Backtester().run(trades))


if __name__ == "__main__":
    main()
