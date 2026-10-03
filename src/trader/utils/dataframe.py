"""DataFrame row extraction helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, overload

import pandas as pd


@overload
def first_value[T](
    row: pd.Series, columns: tuple[str, ...], converter: Callable[[Any], T], fallback: T
) -> T: ...


@overload
def first_value[T](
    row: pd.Series,
    columns: tuple[str, ...],
    converter: Callable[[Any], T],
    fallback: None = None,
) -> T | None: ...


def first_value[T](
    row: pd.Series,
    columns: tuple[str, ...],
    converter: Callable[[Any], T],
    fallback: T | None = None,
) -> T | None:
    """Convert the first non-null candidate column of a row.

    Args:
        row: Source DataFrame row.
        columns: Candidate column names, checked in order.
        converter: Callable applied to the first non-null cell.
        fallback: Value returned when no candidate column holds data.
    """
    cell = next(
        (row[column] for column in columns if column in row.index and pd.notna(row[column])),
        None,
    )
    return converter(cell) if cell is not None else fallback
