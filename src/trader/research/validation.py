"""Purged walk-forward splits over trading dates.

Training always precedes the test block. Labels look ``horizon`` bars ahead, so the last
``horizon`` dates before each test block are purged from training (their labels overlap the test
period), plus ``embargo`` extra dates as a safety margin against serial correlation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class Split:
    """One walk-forward fold, as sets of dates."""

    fold: int
    train_dates: pd.DatetimeIndex
    test_dates: pd.DatetimeIndex


def purged_walk_forward(
    dates: pd.DatetimeIndex,
    n_splits: int = 5,
    horizon: int = 1,
    embargo: int = 0,
    min_train: int = 252,
    expanding: bool = True,
) -> list[Split]:
    """Split sorted unique ``dates`` into ``n_splits`` consecutive test blocks.

    Args:
        dates: Trading dates (duplicates and order are normalised).
        n_splits: Number of test blocks, taken from the dates after ``min_train``.
        horizon: Label horizon in bars; that many dates before each test block are purged.
        embargo: Extra dates dropped between training and test.
        min_train: Dates reserved for the first training window.
        expanding: Grow the training window (True) or roll a fixed ``min_train`` window.

    Returns:
        Folds in time order.

    Raises:
        ValueError: When there are too few dates for the requested folds.
    """
    unique = pd.DatetimeIndex(dates).unique().sort_values()
    gap = horizon + embargo
    test_pool = len(unique) - min_train
    if n_splits < 1 or test_pool < n_splits:
        raise ValueError(
            f"need more than min_train + n_splits dates ({min_train} + {n_splits}), "
            f"got {len(unique)}"
        )
    block = test_pool // n_splits
    splits: list[Split] = []
    for fold in range(n_splits):
        test_start = min_train + fold * block
        test_end = len(unique) if fold == n_splits - 1 else test_start + block
        train_end = test_start - gap
        train_start = 0 if expanding else max(0, train_end - min_train)
        if train_end <= train_start:
            raise ValueError(f"fold {fold} has no training dates after purging {gap} dates")
        splits.append(
            Split(
                fold=fold,
                train_dates=unique[train_start:train_end],
                test_dates=unique[test_start:test_end],
            )
        )
    return splits
