"""Drawdown monitoring."""

from __future__ import annotations

import pandas as pd


class DrawdownMonitor:
    """Track drawdown and halt state."""

    def __init__(self, hard_limit: float = 0.25) -> None:
        self.hard_limit = hard_limit
        self.halted = False

    def evaluate(self, equity_curve: pd.Series) -> float:
        """Evaluate current max drawdown ratio."""
        running_max = equity_curve.cummax()
        drawdown = (running_max - equity_curve) / running_max.replace(0, 1)
        max_dd = float(drawdown.max())
        if max_dd >= self.hard_limit:
            self.halted = True
        return max_dd
