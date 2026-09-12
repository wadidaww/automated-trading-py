"""DataFrame extraction utilities."""

from __future__ import annotations

import pandas as pd


class Extractor:
    """Static utilities for extracting values from DataFrames and Series."""

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
        for column in columns:
            if column in df.columns:
                value = df[column].iloc[0]
                if pd.notna(value):
                    return str(value)
        return fallback

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
