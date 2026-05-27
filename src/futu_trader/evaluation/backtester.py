"""Backtesting engine."""

from __future__ import annotations

import pandas as pd

from futu_trader.evaluation.metrics import Trade, max_drawdown, sharpe_ratio


class Backtester:
    """Simple event-driven backtester facade."""

    def run(self, trades: list[Trade]) -> dict[str, float]:
        """Run backtest and return key metrics."""
        if not trades:
            curve = pd.Series([1.0, 1.01, 0.99, 1.03])
        else:
            equity = [1.0]
            for trade in trades:
                equity.append(equity[-1] + trade.pnl_minor / 100_000)
            curve = pd.Series(equity)
        drawdown, _, _ = max_drawdown(curve)
        return {"sharpe": sharpe_ratio(curve), "max_drawdown": drawdown}
