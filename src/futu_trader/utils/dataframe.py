import pandas as pd


class Extractor:
    @staticmethod
    def _extract_row_value(df: pd.DataFrame, columns: tuple[str, ...], fallback: str) -> str:
        """
        Get first non-null value from candidate columns.
        Example:
            code  stock_code  last_price
        0  00700      00700       500.0
        columns: ("code", "stock_code")
        returns: "00700"
        """
        for column in columns:
            if column in df.columns:
                value = df[column].iloc[0]
                if pd.notna(value):
                    return str(value)
        return fallback

    @staticmethod
    def extract_symbol_from_payload(df: pd.DataFrame, fallback: str = "fallback") -> str:
        """Extract symbol from payload DataFrame."""
        return Extractor._extract_row_value(df, ("code", "stock_code"), fallback)
