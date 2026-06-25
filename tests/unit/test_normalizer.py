from __future__ import annotations

from futu_trader.data.normalizer import DataNormalizer


def test_add_features_contains_expected_columns(ohlcv_df) -> None:
    output = DataNormalizer.add_features(ohlcv_df)
    assert {"rsi_14", "macd", "bb_upper", "vwap", "z_score"}.issubset(output.columns)
