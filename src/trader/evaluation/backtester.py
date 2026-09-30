"""Backtesting engine."""

from __future__ import annotations

import pandas as pd

from trader.evaluation.metrics import Trade, max_drawdown, sharpe_ratio

_PNL_SCALE = 100_000


class Backtester:
    """Simple event-driven backtester facade."""

    @staticmethod
    def _equity_curve(trades: list[Trade]) -> pd.Series:
        """Build the equity curve for a list of trades.

        Args:
            trades: Completed trades in execution order.

        Returns:
            Series of portfolio values starting at 1.0.

        Raises:
            ValueError: When there are no trades; metrics are never fabricated.
        """
        if not trades:
            raise ValueError("backtest has no trades; refusing to report metrics")
        equity = [1.0]
        for trade in trades:
            equity.append(equity[-1] + trade.pnl_minor / _PNL_SCALE)
        return pd.Series(equity)

    def run(self, trades: list[Trade]) -> dict[str, float]:
        """Run backtest and return key metrics."""
        curve = self._equity_curve(trades)
        drawdown, _, _ = max_drawdown(curve)
        return {"sharpe": sharpe_ratio(curve), "max_drawdown": drawdown}
