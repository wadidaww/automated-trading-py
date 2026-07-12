"""Kelly criterion sizing."""

from __future__ import annotations


class KellyCriterion:
    """Compute Kelly and fractional Kelly fractions."""

    def __init__(self, fraction: float = 0.25) -> None:
        self.fraction = fraction

    def calculate(self, win_rate: float, win_loss_ratio: float) -> float:
        """Calculate fractional Kelly allocation."""
        if win_loss_ratio <= 0:
            return 0.0
        full_kelly = win_rate - ((1 - win_rate) / win_loss_ratio)
        return max(0.0, full_kelly * self.fraction)
