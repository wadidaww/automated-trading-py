from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from trader.api.quote_handler import QuoteEvent
from trader.data.features import (
    FEATURE_GROUPS,
    LIVE_FEATURES,
    FeatureSpec,
    build_features,
    live_feature_frame,
)
from trader.pipeline.data_stage import DataStage
from trader.research.synthetic import synthetic_bars


async def test_live_feature_frame_matches_data_stage() -> None:
    bars = synthetic_bars(n_bars=60, seed=3)
    stage = DataStage(window_size=20)
    live = []
    for price in bars["close"]:
        window = await stage.process(
            QuoteEvent(symbol="HK.00700", price=float(price), timestamp=datetime.now(tz=UTC))
        )
        live.append([getattr(window, name) for name in LIVE_FEATURES])
    offline = live_feature_frame(bars["close"], window=20)
    # DataStage returns neutral placeholders while warming up; the frame marks them NaN.
    np.testing.assert_allclose(np.array(live)[4:], offline.to_numpy()[4:], rtol=1e-12)
    assert offline.iloc[:4].isna().all().all()


def test_build_features_every_group_has_no_lookahead() -> None:
    bars = synthetic_bars(n_bars=200, seed=1)
    spec = FeatureSpec(groups=tuple(FEATURE_GROUPS), windows=(5, 20), live_window=30)
    base = build_features(bars, spec)
    cut = 150
    shocked = bars.copy()
    shocked.iloc[cut + 1 :] *= 1.5
    after = build_features(shocked, spec)
    pd.testing.assert_frame_equal(base.iloc[: cut + 1], after.iloc[: cut + 1])
    assert len(base.columns) > 30
    assert base.iloc[-1].notna().all()


def test_build_features_close_only_skips_ohlc_features() -> None:
    bars = synthetic_bars(n_bars=80)[["close"]]
    out = build_features(bars, FeatureSpec(groups=("momentum", "trend", "volatility")))
    assert "ret_20" in out.columns
    assert "adx_14" not in out.columns
    assert "parkinson_5" not in out.columns


def test_build_features_rejects_unknown_group_and_missing_columns() -> None:
    bars = synthetic_bars(n_bars=40)
    with pytest.raises(ValueError, match="unknown feature groups"):
        build_features(bars, FeatureSpec(groups=("astrology",)))
    with pytest.raises(ValueError, match="needs columns"):
        build_features(bars[["close"]], FeatureSpec(groups=("volume",)))


def test_feature_spec_key_is_stable() -> None:
    assert FeatureSpec(groups=("live",), windows=(5,), live_window=50).key == "live|w5|lw50"
