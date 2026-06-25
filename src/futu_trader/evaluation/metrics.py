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
    """Calculate annualized Sharpe ratio."""
    returns = equity_curve.pct_change().dropna()
    if returns.empty or returns.std() == 0:
        return 0.0
    excess = returns.mean() - risk_free_rate / 252
    return float((excess / returns.std()) * (252**0.5))


def sortino_ratio(equity_curve: pd.Series) -> float:
    """Calculate Sortino ratio."""
    returns = equity_curve.pct_change().dropna()
    downside = returns[returns < 0]
    if downside.empty or downside.std() == 0:
        return 0.0
    return float((returns.mean() / downside.std()) * (252**0.5))


def calmar_ratio(equity_curve: pd.Series) -> float:
    """Calculate Calmar ratio."""
    annual = annualized_return(equity_curve)
    dd, _, _ = max_drawdown(equity_curve)
    if dd == 0:
        return 0.0
    return annual / dd


def max_drawdown(equity_curve: pd.Series) -> tuple[float, pd.Timestamp, pd.Timestamp]:
    """Compute max drawdown and timestamps."""
    running_max = equity_curve.cummax()
    drawdown = (running_max - equity_curve) / running_max.replace(0, 1)
    trough = drawdown.idxmax()
    peak = equity_curve.loc[:trough].idxmax()
    return float(drawdown.max()), peak, trough


def win_rate(trades: list[Trade]) -> float:
    """Compute winning trade ratio."""
    if not trades:
        return 0.0
    wins = sum(1 for trade in trades if trade.pnl_minor > 0)
    return wins / len(trades)


def profit_factor(trades: list[Trade]) -> float:
    """Compute profit factor."""
    gains = sum(trade.pnl_minor for trade in trades if trade.pnl_minor > 0)
    losses = -sum(trade.pnl_minor for trade in trades if trade.pnl_minor < 0)
    return float(gains / losses) if losses else 0.0


def annualized_return(equity_curve: pd.Series) -> float:
    """Compute annualized return."""
    if len(equity_curve) < 2:
        return 0.0
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0] - 1
    years = len(equity_curve) / 252
    return float((1 + total_return) ** (1 / years) - 1)


def annualized_volatility(equity_curve: pd.Series) -> float:
    """Compute annualized volatility."""
    returns = equity_curve.pct_change().dropna()
    return float(returns.std() * (252**0.5)) if not returns.empty else 0.0


def turnover(trades: list[Trade], portfolio_value: float) -> float:
    """Compute turnover fraction."""
    if portfolio_value <= 0:
        return 0.0
    traded = sum(abs(trade.notional_minor) for trade in trades)
    return float(traded / portfolio_value)
