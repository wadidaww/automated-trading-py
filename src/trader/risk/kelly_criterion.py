"""Kelly criterion sizing with adaptive fraction."""

from __future__ import annotations


class KellyCriterion:
    """Compute Kelly and fractional Kelly fractions with adaptive sizing.

    Uses a higher fraction when confidence is high (strong conviction)
    and a lower fraction when confidence is moderate (uncertain signals).
    This maximizes gains during high-conviction signals while preserving
    capital during uncertain periods.
    """

    def __init__(self, fraction: float = 0.35, min_fraction: float = 0.15) -> None:
        self._base_fraction = fraction
        self._min_fraction = min_fraction

    def calculate(self, win_rate: float, win_loss_ratio: float) -> float:
        """Calculate adaptive fractional Kelly allocation.

        Args:
            win_rate: Model confidence as proxy for win probability.
            win_loss_ratio: Ratio of average win to average loss.

        Returns:
            Fraction of capital to allocate, scaled by confidence.
        """
        if win_loss_ratio <= 0:
            return 0.0
        full_kelly = win_rate - ((1 - win_rate) / win_loss_ratio)
        if full_kelly <= 0:
            return 0.0
        return max(0.0, full_kelly * self._confidence_fraction(win_rate))

    def _confidence_fraction(self, win_rate: float) -> float:
        """Pick the allocation fraction matching the confidence tier.

        Args:
            win_rate: Model confidence as proxy for win probability.

        Returns:
            Fraction of the full Kelly to allocate for this tier.
        """
        if win_rate >= 0.85:
            return self._base_fraction
        if win_rate >= 0.75:
            return (self._base_fraction + self._min_fraction) / 2
        return self._min_fraction
