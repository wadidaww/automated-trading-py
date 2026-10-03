"""Money and basic statistics helpers."""

from __future__ import annotations

from collections.abc import Sequence

# 1 unit of currency = 100 minor units (cents)
_MINOR_UNITS_FACTOR = 100


def to_minor_units(amount: float) -> int:
    """Convert a currency amount in major units (e.g. HKD) to minor units (cents)."""
    return int(amount * _MINOR_UNITS_FACTOR)


def mean(data: Sequence[float]) -> float:
    """Arithmetic mean.

    Raises:
        ValueError: If data is empty.
    """
    if len(data) == 0:
        raise ValueError("Data list cannot be empty for mean calculation.")
    return sum(data) / len(data)


def std_dev(data: Sequence[float]) -> float:
    """Population standard deviation.

    Raises:
        ValueError: If data is empty.
    """
    if len(data) == 0:
        raise ValueError("Data list cannot be empty for standard deviation calculation.")
    centre = mean(data)
    return (sum((x - centre) ** 2 for x in data) / len(data)) ** 0.5


def z_score(value: float, centre: float, spread: float) -> float:
    """Distance of ``value`` from ``centre`` in units of ``spread``.

    Raises:
        ValueError: If spread is zero.
    """
    if spread == 0:
        raise ValueError("Standard deviation cannot be zero for z-score calculation.")
    return (value - centre) / spread
