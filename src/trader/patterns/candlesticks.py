"""Rule-based candlestick pattern detectors (Japanese candlesticks).

Each detector returns a series aligned with the bars: ``+1`` where a bullish pattern completes
on that bar, ``-1`` for a bearish one and ``0`` otherwise. Neutral patterns (doji, spinning top)
use ``+1`` to mean "present". A pattern is marked on its last bar, so it uses only that bar and
earlier ones.

Candlestick patterns are definitions, so rules are the ground truth for them; the learned
``PatternRecognizer`` is for chart patterns, whose shapes are fuzzy. The thresholds below follow
common TA-Lib / Nison conventions but are heuristics: "long body" means longer than the 10-bar
average body, "doji" means a body within 10% of the bar's range, "trend" is the sign of the
5-bar change before the pattern.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
import pandas as pd

Kind = Literal["bullish", "bearish", "both", "neutral"]


@dataclass(frozen=True, slots=True)
class _Bars:
    """Candle geometry shared by the detectors; ``s(x, k)`` shifts a series ``k`` bars back."""

    o: pd.Series
    h: pd.Series
    low: pd.Series
    c: pd.Series
    body: pd.Series
    range: pd.Series
    upper: pd.Series
    lower: pd.Series
    avg_body: pd.Series
    avg_range: pd.Series
    up_trend: pd.Series
    down_trend: pd.Series

    @classmethod
    def of(cls, bars: pd.DataFrame) -> _Bars:
        o, h, low, c = (bars[col].astype(float) for col in ("open", "high", "low", "close"))
        body = (c - o).abs()
        top, bottom = np.maximum(o, c), np.minimum(o, c)
        prior = c.shift(1) - c.shift(6)
        return cls(
            o=o,
            h=h,
            low=low,
            c=c,
            body=body,
            range=h - low,
            upper=h - top,
            lower=bottom - low,
            avg_body=body.rolling(10, min_periods=5).mean().shift(1),
            avg_range=(h - low).rolling(10, min_periods=5).mean().shift(1),
            up_trend=prior > 0,
            down_trend=prior < 0,
        )

    @staticmethod
    def s(x: pd.Series, k: int) -> pd.Series:
        return x.shift(k)

    def trend_up(self, k: int) -> pd.Series:
        return self.up_trend.shift(k, fill_value=False)

    def trend_down(self, k: int) -> pd.Series:
        return self.down_trend.shift(k, fill_value=False)

    # Per-bar predicates, evaluated k bars back.
    def white(self, k: int = 0) -> pd.Series:
        return self.s(self.c, k) > self.s(self.o, k)

    def black(self, k: int = 0) -> pd.Series:
        return self.s(self.c, k) < self.s(self.o, k)

    def long(self, k: int = 0) -> pd.Series:
        b = self.s(self.body, k)
        return (b > self.s(self.avg_body, k)) & (b >= 0.5 * self.s(self.range, k))

    def short(self, k: int = 0) -> pd.Series:
        return self.s(self.body, k) <= 0.5 * self.s(self.avg_body, k)

    def doji(self, k: int = 0) -> pd.Series:
        r = self.s(self.range, k)
        return (r > 0) & (self.s(self.body, k) <= 0.1 * r)

    def top(self, k: int = 0) -> pd.Series:
        return np.maximum(self.s(self.o, k), self.s(self.c, k))

    def bottom(self, k: int = 0) -> pd.Series:
        return np.minimum(self.s(self.o, k), self.s(self.c, k))

    def mid(self, k: int = 0) -> pd.Series:
        return (self.s(self.o, k) + self.s(self.c, k)) / 2

    def near(self, a: pd.Series, b: pd.Series, k: int = 0, tol: float = 0.05) -> pd.Series:
        return (a - b).abs() <= tol * self.s(self.avg_range, k)


def _signal(bull: pd.Series | None = None, bear: pd.Series | None = None) -> pd.Series:
    base = bull if bull is not None else bear
    assert base is not None
    out = pd.Series(0.0, index=base.index)
    if bull is not None:
        out = out.mask(bull.fillna(False).astype(bool), 1.0)
    if bear is not None:
        out = out.mask(bear.fillna(False).astype(bool), -1.0)
    return out


# --- One bar ----------------------------------------------------------------------------------


def doji(b: _Bars) -> pd.Series:
    return _signal(b.doji())


def long_legged_doji(b: _Bars) -> pd.Series:
    return _signal(b.doji() & (b.upper >= 0.3 * b.range) & (b.lower >= 0.3 * b.range))


def dragonfly_doji(b: _Bars) -> pd.Series:
    return _signal(b.doji() & (b.upper <= 0.1 * b.range) & (b.lower >= 0.6 * b.range))


def gravestone_doji(b: _Bars) -> pd.Series:
    return _signal(bear=b.doji() & (b.lower <= 0.1 * b.range) & (b.upper >= 0.6 * b.range))


def spinning_top(b: _Bars) -> pd.Series:
    shape = ~b.doji() & (b.body <= 0.3 * b.range) & (b.upper > b.body) & (b.lower > b.body)
    return _signal(shape)


def high_wave(b: _Bars) -> pd.Series:
    shape = (b.body <= 0.2 * b.range) & (b.upper >= 0.35 * b.range) & (b.lower >= 0.35 * b.range)
    return _signal(shape & (b.range > 1.3 * b.avg_range))


def marubozu(b: _Bars) -> pd.Series:
    shape = b.long() & (b.body >= 0.95 * b.range)
    return _signal(shape & b.white(), shape & b.black())


def closing_marubozu(b: _Bars) -> pd.Series:
    bull = b.long() & b.white() & (b.upper <= 0.03 * b.range)
    bear = b.long() & b.black() & (b.lower <= 0.03 * b.range)
    return _signal(bull, bear)


def long_line(b: _Bars) -> pd.Series:
    shape = b.body >= 1.5 * b.avg_body
    return _signal(shape & b.white(), shape & b.black())


def _hammer_shape(b: _Bars) -> pd.Series:
    return (b.body > 0) & (b.lower >= 2 * b.body) & (b.upper <= 0.15 * b.range)


def _inverted_shape(b: _Bars) -> pd.Series:
    return (b.body > 0) & (b.upper >= 2 * b.body) & (b.lower <= 0.15 * b.range)


def hammer(b: _Bars) -> pd.Series:
    return _signal(_hammer_shape(b) & b.down_trend)


def hanging_man(b: _Bars) -> pd.Series:
    return _signal(bear=_hammer_shape(b) & b.up_trend)


def inverted_hammer(b: _Bars) -> pd.Series:
    return _signal(_inverted_shape(b) & b.down_trend)


def shooting_star(b: _Bars) -> pd.Series:
    return _signal(bear=_inverted_shape(b) & b.up_trend)


def takuri(b: _Bars) -> pd.Series:
    shape = b.doji() & (b.upper <= 0.05 * b.range) & (b.lower >= 3 * np.maximum(b.body, 1e-12))
    return _signal(shape & b.down_trend)


def belt_hold(b: _Bars) -> pd.Series:
    bull = b.long() & b.white() & (b.lower <= 0.03 * b.range) & b.down_trend
    bear = b.long() & b.black() & (b.upper <= 0.03 * b.range) & b.up_trend
    return _signal(bull, bear)


# --- Two bars ---------------------------------------------------------------------------------


def engulfing(b: _Bars) -> pd.Series:
    bull = (
        b.black(1)
        & b.white()
        & (b.o <= b.s(b.c, 1))
        & (b.c >= b.s(b.o, 1))
        & (b.body > b.s(b.body, 1))
    )
    bear = (
        b.white(1)
        & b.black()
        & (b.o >= b.s(b.c, 1))
        & (b.c <= b.s(b.o, 1))
        & (b.body > b.s(b.body, 1))
    )
    return _signal(bull, bear)


def _inside_prev_body(b: _Bars) -> pd.Series:
    return (b.top() <= b.top(1)) & (b.bottom() >= b.bottom(1)) & (b.body < b.s(b.body, 1))


def harami(b: _Bars) -> pd.Series:
    inside = _inside_prev_body(b) & ~b.doji()
    return _signal(
        inside & b.long(1) & b.black(1) & b.white(), inside & b.long(1) & b.white(1) & b.black()
    )


def harami_cross(b: _Bars) -> pd.Series:
    inside = _inside_prev_body(b) & b.doji() & b.long(1)
    return _signal(inside & b.black(1), inside & b.white(1))


def piercing(b: _Bars) -> pd.Series:
    bull = (
        b.long(1)
        & b.black(1)
        & b.white()
        & (b.o < b.s(b.low, 1))
        & (b.c > b.mid(1))
        & (b.c < b.s(b.o, 1))
    )
    return _signal(bull)


def dark_cloud_cover(b: _Bars) -> pd.Series:
    bear = (
        b.long(1)
        & b.white(1)
        & b.black()
        & (b.o > b.s(b.h, 1))
        & (b.c < b.mid(1))
        & (b.c > b.s(b.o, 1))
    )
    return _signal(bear=bear)


def tweezer(b: _Bars) -> pd.Series:
    top = b.up_trend & b.white(1) & b.black() & b.near(b.h, b.s(b.h, 1))
    bottom = b.down_trend & b.black(1) & b.white() & b.near(b.low, b.s(b.low, 1))
    return _signal(bottom, top)


def kicking(b: _Bars) -> pd.Series:
    def maru(k: int) -> pd.Series:
        return b.long(k) & (b.s(b.body, k) >= 0.9 * b.s(b.range, k))

    bull = maru(1) & b.black(1) & maru(0) & b.white() & (b.low > b.s(b.h, 1))
    bear = maru(1) & b.white(1) & maru(0) & b.black() & (b.h < b.s(b.low, 1))
    return _signal(bull, bear)


def on_neck(b: _Bars) -> pd.Series:
    base = b.down_trend & b.long(1) & b.black(1) & b.white() & (b.o < b.s(b.low, 1))
    return _signal(bear=base & b.near(b.c, b.s(b.low, 1)))


def in_neck(b: _Bars) -> pd.Series:
    base = b.down_trend & b.long(1) & b.black(1) & b.white() & (b.o < b.s(b.low, 1))
    close_in = (b.c >= b.s(b.c, 1)) & (b.c <= b.s(b.c, 1) + 0.1 * b.s(b.body, 1))
    return _signal(bear=base & close_in)


def thrusting(b: _Bars) -> pd.Series:
    base = b.down_trend & b.long(1) & b.black(1) & b.white() & (b.o < b.s(b.low, 1))
    close_in = (b.c > b.s(b.c, 1) + 0.1 * b.s(b.body, 1)) & (b.c < b.mid(1))
    return _signal(bear=base & close_in)


def matching_low(b: _Bars) -> pd.Series:
    return _signal(b.down_trend & b.black(1) & b.black() & b.near(b.c, b.s(b.c, 1)))


def separating_lines(b: _Bars) -> pd.Series:
    bull = b.up_trend & b.black(1) & b.white() & b.long() & b.near(b.o, b.s(b.o, 1))
    bear = b.down_trend & b.white(1) & b.black() & b.long() & b.near(b.o, b.s(b.o, 1))
    return _signal(bull, bear)


def homing_pigeon(b: _Bars) -> pd.Series:
    return _signal(b.down_trend & b.long(1) & b.black(1) & b.black() & _inside_prev_body(b))


def counterattack(b: _Bars) -> pd.Series:
    bull = b.long(1) & b.black(1) & b.long() & b.white() & b.near(b.c, b.s(b.c, 1))
    bear = b.long(1) & b.white(1) & b.long() & b.black() & b.near(b.c, b.s(b.c, 1))
    return _signal(bull, bear)


def doji_star(b: _Bars) -> pd.Series:
    bull = b.down_trend & b.long(1) & b.black(1) & b.doji() & (b.top() < b.s(b.c, 1))
    bear = b.up_trend & b.long(1) & b.white(1) & b.doji() & (b.bottom() > b.s(b.c, 1))
    return _signal(bull, bear)


# --- Three bars -------------------------------------------------------------------------------


def _star(b: _Bars, middle: pd.Series) -> pd.Series:
    bull = b.long(2) & b.black(2) & middle & (b.top(1) < b.s(b.c, 2)) & b.white() & (b.c > b.mid(2))
    bear = (
        b.long(2) & b.white(2) & middle & (b.bottom(1) > b.s(b.c, 2)) & b.black() & (b.c < b.mid(2))
    )
    return _signal(bull, bear)


def morning_evening_star(b: _Bars) -> pd.Series:
    return _star(b, b.short(1) & ~b.doji(1))


def morning_evening_doji_star(b: _Bars) -> pd.Series:
    return _star(b, b.doji(1))


def abandoned_baby(b: _Bars) -> pd.Series:
    bull = (
        b.black(2) & b.doji(1) & (b.s(b.h, 1) < b.s(b.low, 2)) & b.white() & (b.low > b.s(b.h, 1))
    )
    bear = (
        b.white(2) & b.doji(1) & (b.s(b.low, 1) > b.s(b.h, 2)) & b.black() & (b.h < b.s(b.low, 1))
    )
    return _signal(bull, bear)


def _soldiers(b: _Bars, white: bool) -> pd.Series:
    cond = pd.Series(True, index=b.c.index)
    for k in (0, 1, 2):
        colour = b.white(k) if white else b.black(k)
        shadow = b.s(b.upper if white else b.lower, k) <= 0.3 * b.s(b.body, k)
        cond &= colour & b.long(k) & shadow
    for k in (0, 1):
        if white:
            cond &= (b.s(b.c, k) > b.s(b.c, k + 1)) & (b.s(b.o, k) > b.s(b.o, k + 1))
            cond &= b.s(b.o, k) <= b.s(b.c, k + 1)
        else:
            cond &= (b.s(b.c, k) < b.s(b.c, k + 1)) & (b.s(b.o, k) < b.s(b.o, k + 1))
            cond &= b.s(b.o, k) >= b.s(b.c, k + 1)
    return cond


def three_white_soldiers(b: _Bars) -> pd.Series:
    return _signal(_soldiers(b, white=True))


def three_black_crows(b: _Bars) -> pd.Series:
    return _signal(bear=_soldiers(b, white=False) & b.trend_up(2))


def identical_three_crows(b: _Bars) -> pd.Series:
    same_open = b.near(b.o, b.s(b.c, 1)) & b.near(b.s(b.o, 1), b.s(b.c, 2), k=1)
    return _signal(bear=_soldiers(b, white=False) & same_open)


def three_inside(b: _Bars) -> pd.Series:
    inside = (b.top(1) <= b.top(2)) & (b.bottom(1) >= b.bottom(2))
    bull = b.long(2) & b.black(2) & b.white(1) & inside & b.white() & (b.c > b.s(b.o, 2))
    bear = b.long(2) & b.white(2) & b.black(1) & inside & b.black() & (b.c < b.s(b.o, 2))
    return _signal(bull, bear)


def three_outside(b: _Bars) -> pd.Series:
    engulf_bull = b.black(2) & b.white(1) & (b.top(1) >= b.top(2)) & (b.bottom(1) <= b.bottom(2))
    engulf_bear = b.white(2) & b.black(1) & (b.top(1) >= b.top(2)) & (b.bottom(1) <= b.bottom(2))
    bull = engulf_bull & b.white() & (b.c > b.s(b.c, 1))
    bear = engulf_bear & b.black() & (b.c < b.s(b.c, 1))
    return _signal(bull, bear)


def three_line_strike(b: _Bars) -> pd.Series:
    # Three falling black candles then one white candle that wipes them out (a bullish
    # reversal), or the mirror image.
    three_black = b.black(3) & b.black(2) & b.black(1)
    three_black &= (b.s(b.c, 2) < b.s(b.c, 3)) & (b.s(b.c, 1) < b.s(b.c, 2))
    three_white = b.white(3) & b.white(2) & b.white(1)
    three_white &= (b.s(b.c, 2) > b.s(b.c, 3)) & (b.s(b.c, 1) > b.s(b.c, 2))
    bull = three_black & b.white() & (b.o <= b.s(b.c, 1)) & (b.c > b.s(b.o, 3))
    bear = three_white & b.black() & (b.o >= b.s(b.c, 1)) & (b.c < b.s(b.o, 3))
    return _signal(bull, bear)


def tri_star(b: _Bars) -> pd.Series:
    dojis = b.doji(2) & b.doji(1) & b.doji()
    bull = dojis & (b.top(1) < b.bottom(2)) & (b.top(1) < b.bottom())
    bear = dojis & (b.bottom(1) > b.top(2)) & (b.bottom(1) > b.top())
    return _signal(bull, bear)


def two_crows(b: _Bars) -> pd.Series:
    bear = (
        b.trend_up(2)
        & b.long(2)
        & b.white(2)
        & b.black(1)
        & (b.bottom(1) > b.s(b.c, 2))
        & b.black()
        & (b.o < b.s(b.o, 1))
        & (b.o > b.s(b.c, 1))
        & (b.c < b.s(b.c, 2))
        & (b.c > b.s(b.o, 2))
    )
    return _signal(bear=bear)


def upside_gap_two_crows(b: _Bars) -> pd.Series:
    bear = (
        b.long(2)
        & b.white(2)
        & b.black(1)
        & b.short(1)
        & (b.bottom(1) > b.s(b.c, 2))
        & b.black()
        & (b.o > b.s(b.o, 1))
        & (b.c < b.s(b.c, 1))
        & (b.c > b.s(b.c, 2))
    )
    return _signal(bear=bear)


def stick_sandwich(b: _Bars) -> pd.Series:
    bull = (
        b.black(2) & b.white(1) & (b.s(b.c, 1) > b.s(b.c, 2)) & b.black() & b.near(b.c, b.s(b.c, 2))
    )
    return _signal(bull)


def advance_block(b: _Bars) -> pd.Series:
    whites = b.white(2) & b.white(1) & b.white()
    rising = (b.c > b.s(b.c, 1)) & (b.s(b.c, 1) > b.s(b.c, 2))
    shrinking = (b.body < b.s(b.body, 1)) & (b.s(b.body, 1) < b.s(b.body, 2))
    shadows = (b.upper > b.s(b.upper, 1)) | (b.s(b.upper, 1) > b.s(b.upper, 2))
    return _signal(bear=b.trend_up(2) & b.long(2) & whites & rising & shrinking & shadows)


def stalled_pattern(b: _Bars) -> pd.Series:
    bear = (
        b.trend_up(2)
        & b.long(2)
        & b.white(2)
        & b.long(1)
        & b.white(1)
        & (b.s(b.c, 1) > b.s(b.c, 2))
        & b.white()
        & b.short()
        & (b.o >= b.s(b.c, 1) - 0.05 * b.s(b.body, 1))
    )
    return _signal(bear=bear)


def tasuki_gap(b: _Bars) -> pd.Series:
    up = (
        b.white(2)
        & b.white(1)
        & (b.s(b.low, 1) > b.s(b.h, 2))
        & b.black()
        & (b.o < b.s(b.c, 1))
        & (b.o > b.s(b.o, 1))
        & (b.c < b.s(b.o, 1))
        & (b.c > b.s(b.h, 2))
    )
    down = (
        b.black(2)
        & b.black(1)
        & (b.s(b.h, 1) < b.s(b.low, 2))
        & b.white()
        & (b.o > b.s(b.c, 1))
        & (b.o < b.s(b.o, 1))
        & (b.c > b.s(b.o, 1))
        & (b.c < b.s(b.low, 2))
    )
    return _signal(up, down)


def unique_three_river(b: _Bars) -> pd.Series:
    bull = (
        b.long(2)
        & b.black(2)
        & b.black(1)
        & (b.top(1) <= b.top(2))
        & (b.bottom(1) >= b.bottom(2))
        & (b.s(b.low, 1) < b.s(b.low, 2))
        & b.white()
        & b.short()
        & (b.c < b.s(b.c, 1))
    )
    return _signal(bull)


# --- Four or five bars ------------------------------------------------------------------------


def three_methods(b: _Bars) -> pd.Series:
    # Three small candles held inside the first long candle's range, then a continuation.
    inner = pd.Series(True, index=b.c.index)
    for k in (1, 2, 3):
        inner &= b.short(k) | (b.s(b.body, k) < 0.6 * b.s(b.body, 4))
        inner &= (b.s(b.h, k) <= b.s(b.h, 4)) & (b.s(b.low, k) >= b.s(b.low, 4))
    bull = b.long(4) & b.white(4) & inner & b.white() & b.long() & (b.c > b.s(b.c, 4))
    bear = b.long(4) & b.black(4) & inner & b.black() & b.long() & (b.c < b.s(b.c, 4))
    return _signal(bull, bear)


def mat_hold(b: _Bars) -> pd.Series:
    inner = b.short(3) & b.short(2) & b.short(1) & b.black(3)
    inner &= (
        (b.bottom(3) > b.s(b.c, 4))
        & (b.s(b.low, 2) > b.s(b.low, 4))
        & (b.s(b.low, 1) > b.s(b.low, 4))
    )
    highest = pd.concat([b.s(b.h, k) for k in (1, 2, 3)], axis=1).max(axis=1)
    return _signal(b.long(4) & b.white(4) & inner & b.white() & (b.c > highest))


def breakaway(b: _Bars) -> pd.Series:
    bull = (
        b.trend_down(4)
        & b.long(4)
        & b.black(4)
        & b.black(3)
        & (b.top(3) < b.s(b.c, 4))
        & (b.s(b.h, 2) < b.s(b.h, 3))
        & (b.s(b.low, 2) < b.s(b.low, 3))
        & (b.s(b.h, 1) < b.s(b.h, 2))
        & (b.s(b.low, 1) < b.s(b.low, 2))
        & b.white()
        & (b.c > b.s(b.o, 3))
        & (b.c < b.s(b.c, 4))
    )
    bear = (
        b.trend_up(4)
        & b.long(4)
        & b.white(4)
        & b.white(3)
        & (b.bottom(3) > b.s(b.c, 4))
        & (b.s(b.h, 2) > b.s(b.h, 3))
        & (b.s(b.low, 2) > b.s(b.low, 3))
        & (b.s(b.h, 1) > b.s(b.h, 2))
        & (b.s(b.low, 1) > b.s(b.low, 2))
        & b.black()
        & (b.c < b.s(b.o, 3))
        & (b.c > b.s(b.c, 4))
    )
    return _signal(bull, bear)


def ladder_bottom(b: _Bars) -> pd.Series:
    falling = b.black(4) & b.black(3) & b.black(2)
    falling &= (b.s(b.o, 3) < b.s(b.o, 4)) & (b.s(b.o, 2) < b.s(b.o, 3))
    falling &= (b.s(b.c, 3) < b.s(b.c, 4)) & (b.s(b.c, 2) < b.s(b.c, 3))
    fourth = b.black(1) & (b.s(b.upper, 1) > b.s(b.body, 1))
    last = b.white() & (b.o > b.top(1)) & (b.c > b.s(b.h, 1))
    return _signal(falling & fourth & last)


@dataclass(frozen=True, slots=True)
class CandlestickPattern:
    """One detector and what its signal means."""

    detect: Callable[[_Bars], pd.Series]
    kind: Kind
    bars: int
    description: str


CANDLESTICK_PATTERNS: Final[dict[str, CandlestickPattern]] = {
    "doji": CandlestickPattern(doji, "neutral", 1, "Open and close almost equal: indecision"),
    "long_legged_doji": CandlestickPattern(
        long_legged_doji, "neutral", 1, "Doji with long shadows both ways"
    ),
    "dragonfly_doji": CandlestickPattern(
        dragonfly_doji, "bullish", 1, "Doji at the high with a long lower shadow"
    ),
    "gravestone_doji": CandlestickPattern(
        gravestone_doji, "bearish", 1, "Doji at the low with a long upper shadow"
    ),
    "spinning_top": CandlestickPattern(
        spinning_top, "neutral", 1, "Small body with shadows longer than the body"
    ),
    "high_wave": CandlestickPattern(
        high_wave, "neutral", 1, "Wide-range bar with a tiny body and long shadows"
    ),
    "marubozu": CandlestickPattern(marubozu, "both", 1, "Long body with almost no shadows"),
    "closing_marubozu": CandlestickPattern(
        closing_marubozu, "both", 1, "Long body closing at its extreme"
    ),
    "long_line": CandlestickPattern(long_line, "both", 1, "Body 1.5x the recent average"),
    "hammer": CandlestickPattern(hammer, "bullish", 1, "Long lower shadow after a decline"),
    "hanging_man": CandlestickPattern(hanging_man, "bearish", 1, "Hammer shape after an advance"),
    "inverted_hammer": CandlestickPattern(
        inverted_hammer, "bullish", 1, "Long upper shadow after a decline"
    ),
    "shooting_star": CandlestickPattern(
        shooting_star, "bearish", 1, "Long upper shadow after an advance"
    ),
    "takuri": CandlestickPattern(
        takuri, "bullish", 1, "Dragonfly doji with a very long lower shadow after a decline"
    ),
    "belt_hold": CandlestickPattern(
        belt_hold, "both", 1, "Long candle opening at its extreme against the trend"
    ),
    "engulfing": CandlestickPattern(
        engulfing, "both", 2, "Body engulfs the previous opposite-colour body"
    ),
    "harami": CandlestickPattern(
        harami, "both", 2, "Small opposite body inside the previous long body"
    ),
    "harami_cross": CandlestickPattern(
        harami_cross, "both", 2, "Doji inside the previous long body"
    ),
    "piercing": CandlestickPattern(
        piercing, "bullish", 2, "Gap down then close above the prior body's midpoint"
    ),
    "dark_cloud_cover": CandlestickPattern(
        dark_cloud_cover, "bearish", 2, "Gap up then close below the prior body's midpoint"
    ),
    "tweezer": CandlestickPattern(
        tweezer, "both", 2, "Matching highs (top) or lows (bottom) at a turn"
    ),
    "kicking": CandlestickPattern(kicking, "both", 2, "Opposite marubozus separated by a gap"),
    "on_neck": CandlestickPattern(
        on_neck, "bearish", 2, "Weak white candle closing at the prior low"
    ),
    "in_neck": CandlestickPattern(
        in_neck, "bearish", 2, "Weak white candle closing just into the prior body"
    ),
    "thrusting": CandlestickPattern(
        thrusting, "bearish", 2, "White candle closing below the prior midpoint"
    ),
    "matching_low": CandlestickPattern(
        matching_low, "bullish", 2, "Two black candles with the same close"
    ),
    "separating_lines": CandlestickPattern(
        separating_lines, "both", 2, "Opposite candles opening at the same price"
    ),
    "homing_pigeon": CandlestickPattern(
        homing_pigeon, "bullish", 2, "Black candle inside a prior long black body"
    ),
    "counterattack": CandlestickPattern(
        counterattack, "both", 2, "Opposite long candles closing at the same price"
    ),
    "doji_star": CandlestickPattern(doji_star, "both", 2, "Doji gapping away from a long candle"),
    "morning_evening_star": CandlestickPattern(
        morning_evening_star, "both", 3, "Long candle, gapped small body, strong reversal"
    ),
    "morning_evening_doji_star": CandlestickPattern(
        morning_evening_doji_star, "both", 3, "Star pattern with a doji in the middle"
    ),
    "abandoned_baby": CandlestickPattern(
        abandoned_baby, "both", 3, "Doji isolated by gaps on both sides"
    ),
    "three_white_soldiers": CandlestickPattern(
        three_white_soldiers, "bullish", 3, "Three long rising white candles"
    ),
    "three_black_crows": CandlestickPattern(
        three_black_crows, "bearish", 3, "Three long falling black candles after an advance"
    ),
    "identical_three_crows": CandlestickPattern(
        identical_three_crows, "bearish", 3, "Crows each opening at the prior close"
    ),
    "three_inside": CandlestickPattern(
        three_inside, "both", 3, "Harami confirmed by a third candle"
    ),
    "three_outside": CandlestickPattern(
        three_outside, "both", 3, "Engulfing confirmed by a third candle"
    ),
    "three_line_strike": CandlestickPattern(
        three_line_strike, "both", 4, "Three trend candles erased by one"
    ),
    "tri_star": CandlestickPattern(tri_star, "both", 3, "Three dojis with a gapped middle one"),
    "two_crows": CandlestickPattern(
        two_crows, "bearish", 3, "Two black candles after a gap above a long white"
    ),
    "upside_gap_two_crows": CandlestickPattern(
        upside_gap_two_crows, "bearish", 3, "Two black candles above a gap, the second engulfing"
    ),
    "stick_sandwich": CandlestickPattern(
        stick_sandwich, "bullish", 3, "White candle between two blacks with equal closes"
    ),
    "advance_block": CandlestickPattern(
        advance_block, "bearish", 3, "Three white candles losing strength"
    ),
    "stalled_pattern": CandlestickPattern(
        stalled_pattern, "bearish", 3, "Two long whites then a small white: advance stalls"
    ),
    "tasuki_gap": CandlestickPattern(
        tasuki_gap, "both", 3, "Gap that a counter candle fails to close"
    ),
    "unique_three_river": CandlestickPattern(
        unique_three_river, "bullish", 3, "Long black, black harami with a lower low, small white"
    ),
    "three_methods": CandlestickPattern(
        three_methods, "both", 5, "Rising/falling three methods continuation"
    ),
    "mat_hold": CandlestickPattern(
        mat_hold, "bullish", 5, "Gapped pullback held above the first long white"
    ),
    "breakaway": CandlestickPattern(
        breakaway, "both", 5, "Gap, drift, then a candle closing back into the gap"
    ),
    "ladder_bottom": CandlestickPattern(
        ladder_bottom, "bullish", 5, "Four falling blacks then a white breaking higher"
    ),
}


def detect_candlestick_patterns(
    bars: pd.DataFrame, patterns: list[str] | None = None
) -> pd.DataFrame:
    """Run candlestick detectors over OHLC bars.

    Args:
        bars: Bars with ``open``, ``high``, ``low`` and ``close`` (oldest first).
        patterns: Names from ``CANDLESTICK_PATTERNS``; all of them by default.

    Returns:
        One column per pattern with +1 (bullish / present), -1 (bearish) or 0.

    Raises:
        ValueError: On missing columns or an unknown pattern name.
    """
    missing = [c for c in ("open", "high", "low", "close") if c not in bars.columns]
    if missing:
        raise ValueError(f"candlestick patterns need columns {missing}")
    names = list(patterns or CANDLESTICK_PATTERNS)
    unknown = [n for n in names if n not in CANDLESTICK_PATTERNS]
    if unknown:
        raise ValueError(f"unknown candlestick patterns {unknown}")
    geometry = _Bars.of(bars)
    return pd.DataFrame(
        {name: CANDLESTICK_PATTERNS[name].detect(geometry) for name in names}, index=bars.index
    )


def latest_candlestick_patterns(bars: pd.DataFrame) -> dict[str, str]:
    """Patterns completing on the last bar, as ``{name: "bullish" | "bearish" | "neutral"}``."""
    last = detect_candlestick_patterns(bars).iloc[-1]
    found: dict[str, str] = {}
    for name, value in last.items():
        if value == 0:
            continue
        kind = CANDLESTICK_PATTERNS[str(name)].kind
        found[str(name)] = (
            "neutral" if kind == "neutral" else ("bullish" if value > 0 else "bearish")
        )
    return found
