"""Score one model configuration out of sample with purged walk-forward validation."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from trader.model.tabular import TabularModel, Task
from trader.research.dataset import Dataset
from trader.research.scoring import (
    information_coefficients,
    positions_from_scores,
    strategy_returns,
    strategy_stats,
)
from trader.research.validation import purged_walk_forward


@dataclass(frozen=True, slots=True)
class ValidationSettings:
    """Walk-forward layout."""

    n_splits: int = 5
    embargo: int = 5
    min_train: int = 252
    expanding: bool = True


@dataclass(frozen=True, slots=True)
class BacktestSettings:
    """How out-of-sample scores become a daily, cost-aware strategy."""

    cost_bps: float = 20.0
    long_only: bool = True
    deadband: float = 0.0
    periods_per_year: int = 252


@dataclass(frozen=True, slots=True)
class TrialMetrics:
    """Out-of-sample metrics of one configuration."""

    ic: float
    rank_ic: float
    rank_ic_std: float
    icir: float
    n_test_rows: int
    n_obs: int
    sharpe: float
    sharpe_annual: float
    sharpe_annual_gross: float
    annual_return: float
    max_drawdown: float
    turnover: float
    hit_rate: float
    skew: float
    kurtosis: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly dict (NaN becomes None)."""
        return {
            k: (None if isinstance(v, float) and not math.isfinite(v) else v)
            for k, v in asdict(self).items()
        }


def out_of_sample_scores(
    dataset: Dataset,
    estimator: str,
    task: Task,
    params: dict[str, Any],
    horizon: int,
    validation: ValidationSettings,
) -> tuple[pd.Series, pd.Series]:
    """Fit on each training window and score the following test block.

    Returns:
        ``(scores, fold)`` for every test row, indexed like ``dataset.X``.
    """
    splits = purged_walk_forward(
        dataset.dates,
        n_splits=validation.n_splits,
        horizon=horizon,
        embargo=validation.embargo,
        min_train=validation.min_train,
        expanding=validation.expanding,
    )
    scores: list[pd.Series] = []
    folds: list[pd.Series] = []
    for split in splits:
        train = dataset.rows_for(split.train_dates)
        test = dataset.rows_for(split.test_dates)
        if not train.any() or not test.any():
            continue
        model = TabularModel(estimator=estimator, task=task, params=params)
        model.fit(dataset.X[train], dataset.y[train])
        X_test = dataset.X[test]
        scores.append(pd.Series(model.score(X_test), index=X_test.index))
        folds.append(pd.Series(split.fold, index=X_test.index))
    if not scores:
        raise ValueError("walk-forward produced no test predictions")
    return pd.concat(scores), pd.concat(folds)


def score_predictions(
    dataset: Dataset, scores: pd.Series, folds: pd.Series, backtest: BacktestSettings
) -> TrialMetrics:
    """IC statistics and net-of-cost strategy statistics for out-of-sample scores."""
    target = dataset.y.loc[scores.index]
    ic, rank_ic = information_coefficients(scores, target)
    fold_rank_ic = np.array(
        [
            information_coefficients(scores[folds == fold], target[folds == fold])[1]
            for fold in sorted(folds.unique())
        ]
    )
    rank_ic_std = float(fold_rank_ic.std(ddof=1)) if len(fold_rank_ic) > 1 else math.nan
    icir = (
        float(fold_rank_ic.mean()) / rank_ic_std
        if rank_ic_std and math.isfinite(rank_ic_std)
        else math.nan
    )
    positions = positions_from_scores(scores, backtest.deadband, backtest.long_only)
    returns, turnover = strategy_returns(
        positions, dataset.next_return.loc[scores.index], backtest.cost_bps
    )
    stats = strategy_stats(returns, turnover, backtest.periods_per_year)
    gross, _ = strategy_returns(positions, dataset.next_return.loc[scores.index], 0.0)
    gross_stats = strategy_stats(gross, turnover, backtest.periods_per_year)
    return TrialMetrics(
        ic=ic,
        rank_ic=rank_ic,
        rank_ic_std=rank_ic_std,
        icir=icir,
        n_test_rows=len(scores),
        n_obs=stats.n_obs,
        sharpe=stats.sharpe,
        sharpe_annual=stats.sharpe_annual,
        sharpe_annual_gross=gross_stats.sharpe_annual,
        annual_return=stats.annual_return,
        max_drawdown=stats.max_drawdown,
        turnover=stats.turnover,
        hit_rate=stats.hit_rate,
        skew=stats.skew,
        kurtosis=stats.kurtosis,
    )
