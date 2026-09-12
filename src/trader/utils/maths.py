"""Mathematical formula utilities."""

from __future__ import annotations

from collections.abc import Sequence

# Conversion factor: 1 unit of currency = 100 minor units (cents)
_MINOR_UNITS_FACTOR = 100


def to_minor_units(amount: float) -> int:
    """Convert a currency amount to minor units (cents).

    Args:
        amount: Amount in major currency units (e.g. HKD).

    Returns:
        Amount in minor units (e.g. cents).
    """
    return int(amount * _MINOR_UNITS_FACTOR)


class MathFormula:
    """Static mathematical helpers for indicator computation."""

    @staticmethod
    def calc_z_score(value: float, mean: float, std_dev: float) -> float:
        """Calculate the z-score of a value.

        Args:
            value: The value to calculate the z-score for.
            mean: The mean of the dataset.
            std_dev: The standard deviation of the dataset.

        Returns:
            The z-score as a float.

        Raises:
            ValueError: If std_dev is zero.
        """
        if std_dev == 0:
            raise ValueError("Standard deviation cannot be zero for z-score calculation.")
        return (value - mean) / std_dev

    @staticmethod
    def calc_std_dev(data: Sequence[float]) -> float:
        """Calculate the population standard deviation of a dataset.

        Args:
            data: A sequence of numerical values.

        Returns:
            The standard deviation as a float.

        Raises:
            ValueError: If data is empty.
        """
        if len(data) == 0:
            raise ValueError("Data list cannot be empty for standard deviation calculation.")
        mean = MathFormula.calc_mean(data)
        variance = sum((x - mean) ** 2 for x in data) / len(data)
        return variance**0.5

    @staticmethod
    def calc_mean(data: Sequence[float]) -> float:
        """Calculate the arithmetic mean of a dataset.

        Args:
            data: A sequence of numerical values.

        Returns:
            The mean as a float.

        Raises:
            ValueError: If data is empty.
        """
        if len(data) == 0:
            raise ValueError("Data list cannot be empty for mean calculation.")
        return sum(data) / len(data)
