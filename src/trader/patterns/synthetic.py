"""Synthetic chart-pattern examples for training the pattern recognizer.

Each pattern is drawn as a template of key points (time in [0, 1], relative price), jittered,
stretched to a random length, joined by Brownian bridges so the path between key points is
noisy, and turned into OHLC bars with random candle bodies and wicks. Bullish/bearish pairs are
mirror images. The ``none`` class is a plain random walk with random drift, so the model learns
that most windows are not a pattern.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

import numpy as np
import pandas as pd

from trader.patterns.chart import CHART_PATTERNS

Template = Callable[[np.random.Generator], tuple[np.ndarray, np.ndarray]]
NONE: Final = "none"


def _j(rng: np.random.Generator, value: float, spread: float = 0.06) -> float:
    return value + rng.uniform(-spread, spread)


def _alternating(
    rng: np.random.Generator,
    n_touches: int,
    high: Callable[[float], float],
    low: Callable[[float], float],
    start_low: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    xs = np.linspace(0, 1, n_touches)
    ys = []
    for i, x in enumerate(xs):
        on_low = (i % 2 == 0) == start_low
        ys.append((low(x) if on_low else high(x)) + rng.normal(0, 0.02))
    return xs, np.array(ys)


def _with_breakout(
    xs: np.ndarray, ys: np.ndarray, level: float, frac: float = 0.12
) -> tuple[np.ndarray, np.ndarray]:
    xs = np.append(xs * (1 - frac), 1.0)
    return xs, np.append(ys, level)


def _double_top(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    peak = 1.0
    return (
        np.array([0, _j(rng, 0.27), 0.5 + rng.uniform(-0.04, 0.04), _j(rng, 0.75), 1.0]),
        np.array([0, peak, _j(rng, 0.45, 0.1), peak + rng.normal(0, 0.02), _j(rng, 0.3, 0.1)]),
    )


def _triple_top(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    xs = np.array([0, 0.17, 0.33, 0.5, 0.67, 0.83, 1.0]) + np.r_[0, rng.uniform(-0.03, 0.03, 5), 0]
    ys = np.array([0, 1, _j(rng, 0.5), 1, _j(rng, 0.5), 1, _j(rng, 0.3)])
    ys[[1, 3, 5]] += rng.normal(0, 0.02, 3)
    return xs, ys


def _head_and_shoulders(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    shoulder, neck = _j(rng, 0.7), _j(rng, 0.4)
    xs = np.array([0, 0.15, 0.32, 0.5, 0.68, 0.85, 1.0]) + np.r_[0, rng.uniform(-0.03, 0.03, 5), 0]
    ys = np.array(
        [
            0,
            shoulder,
            neck,
            1,
            neck + rng.normal(0, 0.03),
            shoulder + rng.normal(0, 0.03),
            _j(rng, 0.15),
        ]
    )
    return xs, ys


def _ascending_triangle(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = _alternating(rng, int(rng.integers(6, 9)), lambda x: 1.0, lambda x: 0.85 * x)
    return _with_breakout(xs, ys, 1.25)


def _descending_triangle(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = _alternating(
        rng, int(rng.integers(6, 9)), lambda x: 1 - 0.85 * x, lambda x: 0.0, start_low=False
    )
    return _with_breakout(xs, ys, -0.25)


def _symmetrical_triangle(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    return _alternating(rng, int(rng.integers(6, 9)), lambda x: 1 - 0.42 * x, lambda x: 0.42 * x)


def _rising_wedge(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = _alternating(rng, int(rng.integers(6, 9)), lambda x: 0.5 + 0.5 * x, lambda x: 0.9 * x)
    return _with_breakout(xs, ys, 0.45)


def _broadening(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    return _alternating(
        rng, int(rng.integers(6, 9)), lambda x: 0.6 + 0.4 * x, lambda x: 0.4 - 0.4 * x
    )


def _rectangle(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    return _alternating(rng, int(rng.integers(6, 10)), lambda x: 1.0, lambda x: 0.0)


def _channel_up(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    slope = rng.uniform(0.8, 1.6)
    return _alternating(
        rng, int(rng.integers(6, 9)), lambda x: 0.6 + slope * x, lambda x: slope * x
    )


def _flag(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    pole_end = rng.uniform(0.25, 0.4)
    n = int(rng.integers(4, 7))
    xs = np.linspace(pole_end, 0.92, n)
    drift = rng.uniform(0.1, 0.25)
    ys = [
        1.0 - drift * (x - pole_end) / (0.92 - pole_end) - (0.12 if i % 2 else 0.0)
        for i, x in enumerate(xs)
    ]
    return np.r_[0.0, xs, 1.0], np.r_[0.0, ys, 1.1]


def _pennant(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    pole_end = rng.uniform(0.25, 0.4)
    n = int(rng.integers(5, 8))
    xs = np.linspace(pole_end, 0.92, n)
    width = 0.2 * (1 - (xs - pole_end) / (0.92 - pole_end)) + 0.02
    ys = [0.9 + (width[i] / 2 if i % 2 == 0 else -width[i] / 2) for i in range(n)]
    return np.r_[0.0, xs, 1.0], np.r_[0.0, ys, 1.1]


def _cup_and_handle(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    cup_end = rng.uniform(0.7, 0.8)
    x = np.linspace(0, cup_end, 14)
    u = (x / cup_end) * 2 - 1
    y = u**2 * (1 if rng.random() < 0.5 else 1 - 0.3 * (1 - u**2))
    handle_low = 1 - rng.uniform(0.15, 0.3)
    return np.r_[x, (cup_end + 1) / 2 - 0.03, 1.0], np.r_[y, handle_low, 1.08]


def _rounding_bottom(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    x = np.linspace(0, 1, 16)
    power = rng.choice([2.0, 2.0, 4.0])
    return x, np.abs(x * 2 - 1) ** power


def _v_bottom(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    mid = rng.uniform(0.4, 0.6)
    return np.array([0, mid, 1.0]), np.array([1.0, 0.0, _j(rng, 0.95, 0.08)])


def _negate(template: Template) -> Template:
    def mirrored(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
        xs, ys = template(rng)
        return xs, -ys

    return mirrored


TEMPLATES: Final[dict[str, Template]] = {
    "double_top": _double_top,
    "double_bottom": _negate(_double_top),
    "triple_top": _triple_top,
    "triple_bottom": _negate(_triple_top),
    "head_and_shoulders": _head_and_shoulders,
    "inverse_head_and_shoulders": _negate(_head_and_shoulders),
    "ascending_triangle": _ascending_triangle,
    "descending_triangle": _descending_triangle,
    "symmetrical_triangle": _symmetrical_triangle,
    "rising_wedge": _rising_wedge,
    "falling_wedge": _negate(_rising_wedge),
    "broadening": _broadening,
    "rectangle": _rectangle,
    "channel_up": _channel_up,
    "channel_down": _negate(_channel_up),
    "bull_flag": _flag,
    "bear_flag": _negate(_flag),
    "bull_pennant": _pennant,
    "bear_pennant": _negate(_pennant),
    "cup_and_handle": _cup_and_handle,
    "inverse_cup_and_handle": _negate(_cup_and_handle),
    "rounding_bottom": _rounding_bottom,
    "rounding_top": _negate(_rounding_bottom),
    "v_bottom": _v_bottom,
    "v_top": _negate(_v_bottom),
}
assert set(TEMPLATES) == set(CHART_PATTERNS)

PATTERN_CLASSES: Final[tuple[str, ...]] = (*CHART_PATTERNS, NONE)
"""Recognizer classes: every chart pattern plus ``none``."""


def _path(
    xs: np.ndarray, ys: np.ndarray, n: int, noise: float, rng: np.random.Generator
) -> np.ndarray:
    """Close prices through the key points, with Brownian-bridge noise between them."""
    t = np.linspace(0, 1, n)
    order = np.argsort(xs)
    base = np.interp(t, xs[order], ys[order])
    steps = rng.normal(0, noise / np.sqrt(n), n)
    walk = np.cumsum(steps)
    knots = np.clip(np.searchsorted(t, xs[order]), 0, n - 1)
    # Pin the walk to zero at every key point (a bridge per segment).
    pinned = walk - np.interp(np.arange(n), knots, walk[knots])
    return base + pinned


def _ohlc(close_rel: np.ndarray, rng: np.random.Generator, scale: float) -> pd.DataFrame:
    """OHLC bars around a relative close path, priced near 100."""
    close = 100 * np.exp(close_rel * scale)
    step = np.abs(np.diff(np.log(close), prepend=np.log(close[0])))
    candle = np.maximum(step.mean(), 1e-4)
    open_ = np.r_[close[0], close[:-1]] * np.exp(rng.normal(0, candle / 3, len(close)))
    wick_hi = np.abs(rng.normal(0, candle / 1.5, len(close)))
    wick_lo = np.abs(rng.normal(0, candle / 1.5, len(close)))
    high = np.maximum(open_, close) * np.exp(wick_hi)
    low = np.minimum(open_, close) * np.exp(-wick_lo)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


def pattern_example(
    name: str,
    rng: np.random.Generator,
    length: int | None = None,
    noise: float | None = None,
) -> pd.DataFrame:
    """One synthetic OHLC window showing ``name`` (or a random walk for ``none``)."""
    n = int(length or rng.integers(30, 160))
    if name == NONE:
        drift = rng.normal(0, 0.6)
        steps = rng.normal(drift / n, 1 / np.sqrt(n), n)
        rel = np.cumsum(steps)
    elif name in TEMPLATES:
        xs, ys = (np.asarray(v, dtype=float) for v in TEMPLATES[name](rng))
        ys = ys + rng.normal(0, 0.05) * xs  # slight tilt
        level = noise if noise is not None else rng.uniform(0.08, 0.4)
        # Real windows rarely frame a pattern exactly: add a little unrelated price before
        # and after it.
        lead, tail = (int(n * rng.uniform(0, 0.15)) for _ in range(2))
        core = _path(xs, ys, max(n - lead - tail, 10), level, rng)
        pre = core[0] - np.cumsum(rng.normal(0, 1 / np.sqrt(n), lead))[::-1]
        post = core[-1] + np.cumsum(rng.normal(0, 1 / np.sqrt(n), tail))
        rel = np.r_[pre, core, post]
    else:
        raise ValueError(f"unknown pattern {name!r}; known: {sorted(PATTERN_CLASSES)}")
    return _ohlc(rel, rng, scale=rng.uniform(0.05, 0.3))


def pattern_dataset(
    n_per_class: int, seed: int = 0, classes: tuple[str, ...] = PATTERN_CLASSES
) -> tuple[list[pd.DataFrame], np.ndarray]:
    """``n_per_class`` examples of each class; returns the windows and integer labels."""
    rng = np.random.default_rng(seed)
    windows: list[pd.DataFrame] = []
    labels: list[int] = []
    for label, name in enumerate(classes):
        for _ in range(n_per_class):
            windows.append(pattern_example(name, rng))
            labels.append(label)
    return windows, np.asarray(labels)
