from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trader.data import technical
from trader.data.features import FeatureSpec
from trader.patterns.__main__ import main
from trader.patterns.candlesticks import (
    CANDLESTICK_PATTERNS,
    detect_candlestick_patterns,
    latest_candlestick_patterns,
)
from trader.patterns.chart import (
    CHART_PATTERNS,
    chart_pattern_frame,
    detect_chart_patterns,
    patterns_frame,
    zigzag_pivots,
)
from trader.patterns.recognizer import (
    PatternRecognizer,
    TrainSettings,
    encode_window,
    weak_labels,
)
from trader.patterns.stats import pattern_outcomes
from trader.patterns.synthetic import PATTERN_CLASSES, pattern_example
from trader.research.synthetic import synthetic_bars


def _candles(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)


def _downtrend(n: int = 10, start: float = 120.0) -> list[tuple[float, float, float, float]]:
    rows = []
    price = start
    for _ in range(n):
        rows.append((price, price + 0.3, price - 2.3, price - 2.0))
        price -= 2.0
    return rows


def _uptrend(n: int = 10, start: float = 100.0) -> list[tuple[float, float, float, float]]:
    rows = []
    price = start
    for _ in range(n):
        rows.append((price, price + 2.3, price - 0.3, price + 2.0))
        price += 2.0
    return rows


def _last(bars: pd.DataFrame, name: str) -> float:
    return float(detect_candlestick_patterns(bars, [name])[name].iloc[-1])


def test_single_bar_patterns_respect_the_trend() -> None:
    down = _downtrend()
    hammer = [*down, (99.5, 100.0, 95.0, 99.8)]
    assert _last(_candles(hammer), "hammer") == 1
    assert _last(_candles(hammer), "hanging_man") == 0
    up = _uptrend()
    star = [*up, (120.0, 125.0, 119.9, 120.3)]
    assert _last(_candles(star), "shooting_star") == -1
    assert _last(_candles([*up, (120.0, 121.0, 119.0, 120.02)]), "doji") == 1


def test_two_and_three_bar_reversals() -> None:
    down = _downtrend()
    engulf = [*down, (100.0, 100.2, 97.0, 97.5), (97.0, 101.5, 96.8, 101.0)]
    assert _last(_candles(engulf), "engulfing") == 1
    morning = [
        *down,
        (100.0, 100.2, 95.8, 96.0),
        (95.0, 95.6, 94.4, 95.2),
        (95.6, 99.5, 95.5, 99.0),
    ]
    assert _last(_candles(morning), "morning_evening_star") == 1
    soldiers = [
        *_downtrend(),
        (100, 103.2, 99.9, 103),
        (102, 106.2, 101.9, 106),
        (105, 109.2, 104.9, 109),
    ]
    assert _last(_candles(soldiers), "three_white_soldiers") == 1


def test_detector_validation_and_latest() -> None:
    bars = synthetic_bars(n_bars=300)
    frame = detect_candlestick_patterns(bars)
    assert list(frame.columns) == list(CANDLESTICK_PATTERNS)
    assert set(np.unique(frame.to_numpy())) <= {-1.0, 0.0, 1.0}
    assert isinstance(latest_candlestick_patterns(bars), dict)
    with pytest.raises(ValueError, match="unknown candlestick"):
        detect_candlestick_patterns(bars, ["unicorn"])
    with pytest.raises(ValueError, match="need columns"):
        detect_candlestick_patterns(bars[["close"]])


def _path_bars(points: list[float], per_leg: int = 20) -> pd.DataFrame:
    close = np.concatenate(
        [
            np.linspace(a, b, per_leg, endpoint=False)
            for a, b in zip(points, points[1:], strict=False)
        ]
        + [np.array([points[-1]])]
    )
    open_ = np.r_[close[0], close[:-1]]
    rows = [(o, max(o, c) + 0.1, min(o, c) - 0.1, c) for o, c in zip(open_, close, strict=True)]
    return _candles(rows)


def _names(bars: pd.DataFrame) -> set[str]:
    return {p.name for p in detect_chart_patterns(bars)}


def test_zigzag_pivots_alternate_and_confirm_later() -> None:
    pivots = zigzag_pivots(_path_bars([100, 130, 112, 130, 100]))
    assert [p.kind for p in pivots] == ["low", "high", "low", "high"]
    assert all(p.confirmed > p.index for p in pivots)


def test_classic_reversal_patterns_are_found() -> None:
    double_top = _path_bars([100, 130, 112, 130, 100])
    (found,) = (p for p in detect_chart_patterns(double_top) if p.name == "double_top")
    assert found.direction == -1
    assert found.breakout == pytest.approx(112, abs=0.5)
    assert found.target < found.breakout
    assert found.detected_at > 60  # only after the second top has turned down
    assert "double_bottom" in _names(_path_bars([130, 100, 118, 100, 130]))
    assert "head_and_shoulders" in _names(_path_bars([100, 120, 110, 135, 110, 120, 95]))
    assert "inverse_head_and_shoulders" in _names(_path_bars([135, 115, 125, 100, 125, 115, 140]))


def test_continuation_patterns_are_found() -> None:
    triangle = _path_bars([100, 130, 110, 130, 118, 130, 124])
    assert "ascending_triangle" in _names(triangle)
    flag = np.r_[
        np.full(20, 100.0),
        np.linspace(100, 120, 8),
        120 - np.arange(10) * 0.3 + np.tile([0.4, -0.4], 5),
        124.0,
    ]
    open_ = np.r_[flag[0], flag[:-1]]
    rows = [(o, max(o, c) + 0.1, min(o, c) - 0.1, c) for o, c in zip(open_, flag, strict=True)]
    hits = [p for p in detect_chart_patterns(_candles(rows)) if p.name == "bull_flag"]
    assert hits
    assert hits[0].detected_at == len(flag) - 1


def test_chart_pattern_frame_and_table() -> None:
    bars = _path_bars([100, 130, 112, 130, 100])
    frame = chart_pattern_frame(bars)
    assert list(frame.columns) == [f"chp_{n}" for n in CHART_PATTERNS]
    assert (frame["chp_double_top"] == -1).sum() > 0
    table = patterns_frame(detect_chart_patterns(bars), bars.index)
    assert {"name", "detected_at_time", "target"} <= set(table.columns)
    assert patterns_frame([]).empty


def test_technical_building_blocks() -> None:
    rising = pd.Series(np.arange(1.0, 61.0))
    assert technical.rsi(rising).iloc[-1] == pytest.approx(100.0)
    assert technical.wma(pd.Series([1.0, 2.0, 3.0]), 3).iloc[-1] == pytest.approx(14 / 6)
    assert technical.kama(pd.Series(np.full(50, 7.0))).iloc[-1] == pytest.approx(7.0)
    bars = _candles([(c - 0.5, c + 1, c - 1, c) for c in np.arange(100.0, 160.0)])
    assert technical.supertrend(bars)[1].iloc[-1] == 1
    assert technical.parabolic_sar(bars)[1].iloc[-1] == 1
    assert technical.atr(bars).iloc[-1] == pytest.approx(2.0, rel=0.05)
    k, d = technical.stochastic(bars)
    assert k.iloc[-1] > 80
    assert d.iloc[-1] > 80


def test_technical_groups_are_finite_after_warm_up() -> None:
    bars = synthetic_bars(n_bars=700)
    spec = FeatureSpec(windows=(5, 20))
    for name, group in technical.TECHNICAL_GROUPS.items():
        frame = group(bars, spec).iloc[400:]
        assert not frame.empty, name
        assert np.isfinite(frame.to_numpy()).all(), name
    with pytest.raises(ValueError, match="needs columns"):
        technical.momentum_ext_group(bars[["close"]], spec)


def test_synthetic_examples_are_valid_ohlc() -> None:
    rng = np.random.default_rng(0)
    for name in PATTERN_CLASSES:
        window = pattern_example(name, rng, length=60)
        assert len(window) == 60
        assert (window["high"] >= window[["open", "close"]].max(axis=1) - 1e-9).all()
        assert (window["low"] <= window[["open", "close"]].min(axis=1) + 1e-9).all()
    with pytest.raises(ValueError, match="unknown pattern"):
        pattern_example("unicorn", rng)


def test_encode_window_is_scaled_and_resampled() -> None:
    encoded = encode_window(synthetic_bars(n_bars=90), steps=32)
    assert encoded.shape == (4, 32)
    assert encoded.min() >= 0
    assert encoded.max() <= 1
    with pytest.raises(ValueError, match="two bars"):
        encode_window(synthetic_bars(n_bars=1))


def test_recognizer_trains_scans_and_round_trips(tmp_path) -> None:
    bars = synthetic_bars(n_bars=400)
    extra = weak_labels(bars, detect_chart_patterns(bars)[:2])
    assert [name for _, name in extra].count("none") == 2
    model = PatternRecognizer(width=8)
    with pytest.raises(RuntimeError, match="not trained"):
        model.predict_proba([bars])
    metrics = model.fit(TrainSettings(n_per_class=6, epochs=1, batch_size=32), extra=extra)
    assert 0 <= metrics["val_accuracy"] <= 1
    probs = model.predict_proba([bars.iloc[:60], bars.iloc[100:200]])
    np.testing.assert_allclose(probs.sum(axis=1), 1.0, rtol=1e-5)
    found = model.scan(bars, lengths=(40, 80), stride=20, threshold=0.0)
    assert {"pattern", "probability", "start_time"} <= set(found.columns)
    assert len(model.classify(bars.iloc[:50], top=2)) == 2
    path = tmp_path / "patterns.pt"
    model.save(str(path))
    loaded = PatternRecognizer.load(str(path))
    np.testing.assert_allclose(loaded.predict_proba([bars.iloc[:60]]), probs[:1], rtol=1e-5)
    with pytest.raises(ValueError, match="none"):
        PatternRecognizer(classes=("double_top",))


def test_pattern_outcomes_and_cli(tmp_path, capsys) -> None:
    table = pattern_outcomes({"A": synthetic_bars(n_bars=600)}, horizon=5)
    assert (table["count"] > 0).all()
    assert {"mean_signed_return", "hit_rate", "t_stat"} <= set(table.columns)
    model_path = tmp_path / "model.pt"
    main(
        [
            "train",
            "--synthetic",
            "--symbols",
            "A",
            "--per-class",
            "3",
            "--epochs",
            "1",
            "--out",
            str(model_path),
        ]
    )
    main(["scan", "--synthetic", "--symbol", "A", "--model", str(model_path), "--threshold", "0.0"])
    main(["stats", "--synthetic", "--symbols", "A", "--out", str(tmp_path / "stats.csv")])
    out = capsys.readouterr().out
    assert "Neural network chart patterns" in out
    assert (tmp_path / "stats.csv").exists()
