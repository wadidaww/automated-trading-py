"""Run local backtest."""

from __future__ import annotations

from trader.evaluation.backtester import Backtester


def main() -> None:
    """CLI entrypoint."""
    result = Backtester().run([])
    print(result)


if __name__ == "__main__":
    main()
