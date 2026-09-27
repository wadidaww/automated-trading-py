"""DataFrame extraction utilities."""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd


class Extractor:
    """Static utilities for extracting values from DataFrames and Series."""

    @staticmethod
    def _non_null_values(df: pd.DataFrame, columns: tuple[str, ...]) -> Iterator[object]:
        """Yield the first cell of every candidate column that holds a value.

        Args:
            df: Source DataFrame.
            columns: Candidate column names to check.

        Yields:
            First cell of each present column, skipping nulls.
        """
        for column in columns:
            if column in df.columns:
                value = df[column].iloc[0]
                if pd.notna(value):
                    yield value

    @staticmethod
    def extract_row_value(df: pd.DataFrame, columns: tuple[str, ...], fallback: str) -> str:
        """Get first non-null value from candidate columns in a DataFrame.

        Args:
            df: Source DataFrame.
            columns: Candidate column names to check.
            fallback: Value to return if no match found.

        Returns:
            First non-null string value, or fallback.
        """
        value = next(Extractor._non_null_values(df, columns), None)
        return fallback if value is None else str(value)

    @staticmethod
    def extract_symbol_from_payload(df: pd.DataFrame, fallback: str = "fallback") -> str:
        """Extract symbol code from a payload DataFrame.

        Args:
            df: Payload DataFrame with stock code columns.
            fallback: Value to return if no symbol found.

        Returns:
            Symbol string.
        """
        return Extractor.extract_row_value(df, ("code", "stock_code"), fallback)
