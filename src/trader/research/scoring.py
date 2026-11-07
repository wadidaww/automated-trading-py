"""Out-of-sample scoring: information coefficients, a cost-aware daily strategy, deflated Sharpe.

Conventions: Sharpe ratios inside this module are per period (not annualised) unless the name says
``annual``. The deflated Sharpe ratio follows Bailey and Lopez de Prado (2014).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Final

import numpy as np
import pandas as pd

MIN_SHARPE_OBS: Final = 30
_EULER_GAMMA: Final = 0.5772156649015329
_NORMAL: Final = NormalDist()


@dataclass(frozen=True, slots=True)
class StrategyStats:
    """Net-of-cost performance of the score-following strategy."""

    n_obs: int
    sharpe: float
    sharpe_annual: float
    annual_return: float
    max_drawdown: float
    turnover: float
    hit_rate: float
    skew: float
    kurtosis: float


def information_coefficients(scores: pd.Series, target: pd.Series) -> tuple[float, float]:
    """Pearson and Spearman correlation between predictions and realised targets."""
    frame = pd.concat([scores, target], axis=1).dropna()
    if len(frame) < 3 or frame.iloc[:, 0].std() == 0 or frame.iloc[:, 1].std() == 0:
        return 0.0, 0.0
    pearson = float(frame.iloc[:, 0].corr(frame.iloc[:, 1]))
    spearman = float(frame.iloc[:, 0].rank().corr(frame.iloc[:, 1].rank()))
    return pearson, spearman


def positions_from_scores(
    scores: pd.Series, deadband: float = 0.0, long_only: bool = True
) -> pd.Series:
    """Target position per row: +1 above ``deadband``, -1 below ``-deadband`` (0 if long-only)."""
    position = np.sign(scores.where(scores.abs() > deadband, 0.0))
    if long_only:
        position = position.clip(lower=0.0)
    return position.astype(float)


def strategy_returns(
    positions: pd.Series, next_returns: pd.Series, cost_bps: float = 0.0
) -> tuple[pd.Series, pd.Series]:
    """Equal-weight portfolio return per date, net of costs, and its turnover.

    Args:
        positions: Position held from the close of ``t`` to the close of ``t+1``, indexed by
            ``(timestamp, symbol)``.
        next_returns: Return from the close of ``t`` to ``t+1`` on the same index.
        cost_bps: One-way cost per unit of position traded, in basis points.

    Returns:
        ``(net_returns, turnover)`` indexed by timestamp.
    """
    frame = pd.DataFrame({"position": positions, "ret": next_returns}).dropna()
    trades = frame.groupby(level="symbol")["position"].diff().abs()
    trades = trades.fillna(frame["position"].abs())
    frame["pnl"] = frame["position"] * frame["ret"] - trades * cost_bps / 1e4
    frame["traded"] = trades
    per_date = frame.groupby(level="timestamp")[["pnl", "traded"]].mean()
    return per_date["pnl"], per_date["traded"]


def strategy_stats(
    returns: pd.Series, turnover: pd.Series, periods_per_year: int = 252
) -> StrategyStats:
    """Summarise a per-period net return series; Sharpe is NaN below ``MIN_SHARPE_OBS``."""
    n_obs = len(returns)
    std = float(returns.std()) if n_obs > 1 else 0.0
    enough = n_obs >= MIN_SHARPE_OBS and std > 0
    sharpe = float(returns.mean()) / std if enough else math.nan
    equity = (1 + returns).cumprod()
    drawdown = float((1 - equity / equity.cummax()).max()) if n_obs else 0.0
    years = n_obs / periods_per_year
    final = float(equity.iloc[-1]) if n_obs else 1.0
    annual = final ** (1 / years) - 1 if years > 0 and final > 0 else math.nan
    active = returns[returns != 0]
    return StrategyStats(
        n_obs=n_obs,
        sharpe=sharpe,
        sharpe_annual=sharpe * math.sqrt(periods_per_year),
        annual_return=annual,
        max_drawdown=drawdown,
        turnover=float(turnover.mean()) if len(turnover) else 0.0,
        hit_rate=float((active > 0).mean()) if len(active) else math.nan,
        skew=float(returns.skew()) if enough else 0.0,
        kurtosis=float(returns.kurt()) + 3 if enough else 3.0,
    )


def expected_max_sharpe(trial_sharpe_variance: float, n_trials: int) -> float:
    """Expected maximum per-period Sharpe among ``n_trials`` skill-less trials."""
    if n_trials < 2 or trial_sharpe_variance <= 0:
        return 0.0
    first = _NORMAL.inv_cdf(1 - 1 / n_trials)
    second = _NORMAL.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(trial_sharpe_variance) * ((1 - _EULER_GAMMA) * first + _EULER_GAMMA * second)


def deflated_sharpe_ratio(
    sharpe: float,
    n_obs: int,
    skew: float,
    kurtosis: float,
    trial_sharpe_variance: float,
    n_trials: int,
) -> float:
    """Probability that the true Sharpe beats the best of ``n_trials`` lucky trials.

    Args:
        sharpe: Observed per-period Sharpe of the selected trial.
        n_obs: Number of return observations behind ``sharpe``.
        skew: Skewness of those returns.
        kurtosis: Kurtosis (not excess) of those returns.
        trial_sharpe_variance: Variance of per-period Sharpe across all trials tried.
        n_trials: Number of trials tried (the whole grid, not just the survivors).

    Returns:
        A probability in [0, 1]; above 0.95 is the usual bar. NaN if inputs are unusable.
    """
    if not math.isfinite(sharpe) or n_obs < 2:
        return math.nan
    benchmark = expected_max_sharpe(trial_sharpe_variance, n_trials)
    denom = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe**2
    if denom <= 0:
        return math.nan
    return _NORMAL.cdf((sharpe - benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom))
