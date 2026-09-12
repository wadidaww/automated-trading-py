"""Performance metrics."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class Trade:
    """Trade record."""

    pnl_minor: int
    notional_minor: int


def sharpe_ratio(equity_curve: pd.Series, risk_free_rate: float = 0.04) -> float:
    """Calculate annualized Sharpe ratio.

    Args:
        equity_curve: Series of portfolio values over time.
        risk_free_rate: Annual risk-free rate (default 4%).

    Returns:
        Annualized Sharpe ratio.
    """
    returns = equity_curve.pct_change().dropna()
    if returns.empty or returns.std() == 0:
        return 0.0
    excess = returns.mean() - risk_free_rate / 252
    return float((excess / returns.std()) * (252**0.5))


def max_drawdown(equity_curve: pd.Series) -> tuple[float, pd.Timestamp, pd.Timestamp]:
    """Compute max drawdown and timestamps.

    Args:
        equity_curve: Series of portfolio values over time.

    Returns:
        Tuple of (max_drawdown_fraction, peak_timestamp, trough_timestamp).
    """
    running_max = equity_curve.cummax()
    drawdown = (running_max - equity_curve) / running_max.replace(0, 1)
    trough = drawdown.idxmax()
    peak = equity_curve.loc[:trough].idxmax()
    return float(drawdown.max()), peak, trough
