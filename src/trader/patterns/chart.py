"""Rule-based chart pattern detection on swing pivots.

Prices are reduced to alternating swing highs and lows with an ATR-scaled zigzag. A swing is
only known once price has reversed by the threshold, so every pivot carries the bar at which it
was ``confirmed``. Pattern rules run each time a pivot is confirmed and see only pivots confirmed
so far, which keeps detections free of look-ahead: a pattern's ``detected_at`` bar is the first
bar at which it could have been seen.

Flags and pennants are too small for the swing pivots, so they are found by a separate bar scan:
a fast move (the pole), a tight consolidation, and a breakout close.

These rules are explicit and explainable but brittle; ``recognizer.PatternRecognizer`` learns
the same shapes from examples and copes better with noisy, imperfect patterns.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Final, Literal

import numpy as np
import pandas as pd

from trader.data.technical import atr

PivotKind = Literal["high", "low"]


@dataclass(frozen=True, slots=True)
class Pivot:
    """A confirmed swing high or low (positions are bar offsets into the input)."""

    index: int
    price: float
    kind: PivotKind
    confirmed: int


@dataclass(frozen=True, slots=True)
class ChartPattern:
    """One detected chart pattern.

    ``direction`` is the usual implication: +1 bullish, -1 bearish, 0 neutral. ``breakout`` is
    the level whose break confirms the pattern (neckline, triangle edge, rim) and ``target`` the
    textbook measured-move objective; both are guides, not forecasts.
    """

    name: str
    direction: int
    start: int
    end: int
    detected_at: int
    breakout: float
    target: float
    confidence: float
    pivots: tuple[int, ...] = field(default=())

    def to_dict(self, index: pd.Index | None = None) -> dict[str, object]:
        """Plain dict; with ``index`` the bar positions are also given as timestamps."""
        out: dict[str, object] = asdict(self)
        if index is not None:
            for key in ("start", "end", "detected_at"):
                out[f"{key}_time"] = index[int(out[key])]  # type: ignore[call-overload]
        return out


@dataclass(frozen=True, slots=True)
class ChartSettings:
    """Detection thresholds, in ATR units unless stated."""

    zigzag_atr: float = 2.0
    atr_period: int = 14
    equal_tolerance: float = 1.0
    min_height: float = 2.0
    flat_tolerance: float = 1.0
    min_spacing: int = 4
    pole_atr: float = 4.0
    pole_lengths: tuple[int, ...] = (5, 10, 15)
    flag_lengths: tuple[int, ...] = (5, 8, 12, 16)
    hold: int = 10


def zigzag_pivots(
    bars: pd.DataFrame, threshold_atr: float = 2.0, atr_period: int = 14
) -> list[Pivot]:
    """Alternating swing highs and lows; a swing confirms after a ``threshold_atr`` reversal."""
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    tr = atr(bars, atr_period).bfill().to_numpy(dtype=float)
    fallback = (bars["high"] - bars["low"]).expanding().mean().to_numpy(dtype=float)
    threshold = threshold_atr * np.where(np.isnan(tr), fallback, tr)
    pivots: list[Pivot] = []
    if len(high) == 0:
        return pivots
    direction = 0
    hi_i = lo_i = 0
    for i in range(1, len(high)):
        if direction >= 0 and high[i] > high[hi_i]:
            hi_i = i
        if direction <= 0 and low[i] < low[lo_i]:
            lo_i = i
        if direction >= 0 and high[hi_i] - low[i] >= threshold[i] and hi_i < i:
            if direction == 0 and lo_i < hi_i:
                pivots.append(Pivot(lo_i, float(low[lo_i]), "low", i))
            pivots.append(Pivot(hi_i, float(high[hi_i]), "high", i))
            direction, lo_i = -1, i
        elif direction <= 0 and high[i] - low[lo_i] >= threshold[i] and lo_i < i:
            if direction == 0 and hi_i < lo_i:
                pivots.append(Pivot(hi_i, float(high[hi_i]), "high", i))
            pivots.append(Pivot(lo_i, float(low[lo_i]), "low", i))
            direction, hi_i = 1, i
    return pivots


@dataclass(frozen=True, slots=True)
class _Context:
    close: np.ndarray
    atr: float
    settings: ChartSettings

    def tol(self) -> float:
        return self.settings.equal_tolerance * self.atr

    def height(self) -> float:
        return self.settings.min_height * self.atr


def _kinds(pivots: list[Pivot]) -> str:
    return "".join("H" if p.kind == "high" else "L" for p in pivots)


def _spaced(pivots: list[Pivot], gap: int) -> bool:
    return all(b.index - a.index >= gap for a, b in zip(pivots, pivots[1:], strict=False))


def _make(
    name: str,
    direction: int,
    pivots: list[Pivot],
    breakout: float,
    target: float,
    confidence: float,
) -> ChartPattern:
    return ChartPattern(
        name=name,
        direction=direction,
        start=pivots[0].index,
        end=pivots[-1].index,
        detected_at=pivots[-1].confirmed,
        breakout=float(breakout),
        target=float(target),
        confidence=float(np.clip(confidence, 0.0, 1.0)),
        pivots=tuple(p.index for p in pivots),
    )


def _flip(pivots: list[Pivot]) -> list[Pivot]:
    """Mirror pivots (negate prices) so bullish rules can reuse the bearish ones."""
    return [
        Pivot(p.index, -p.price, "low" if p.kind == "high" else "high", p.confirmed) for p in pivots
    ]


def _double_top(pv: list[Pivot], ctx: _Context) -> tuple[float, float, float, list[Pivot]] | None:
    p = pv[-4:]
    if len(p) < 4 or _kinds(p) != "LHLH" or not _spaced(p, ctx.settings.min_spacing):
        return None
    l0, h1, l2, h3 = (x.price for x in p)
    top = (h1 + h3) / 2
    if abs(h1 - h3) > ctx.tol() or top - l2 < ctx.height() or l2 <= l0:
        return None
    return l2, l2 - (top - l2), 1 - abs(h1 - h3) / (ctx.tol() + 1e-12) / 2, p


def _triple_top(pv: list[Pivot], ctx: _Context) -> tuple[float, float, float, list[Pivot]] | None:
    p = pv[-5:]
    if len(p) < 5 or _kinds(p) != "HLHLH" or not _spaced(p, ctx.settings.min_spacing):
        return None
    h1, l2, h3, l4, h5 = (x.price for x in p)
    tops = np.array([h1, h3, h5])
    neck = min(l2, l4)
    if tops.max() - tops.min() > ctx.tol() or tops.mean() - max(l2, l4) < ctx.height():
        return None
    return (
        neck,
        neck - (tops.mean() - neck),
        1 - (tops.max() - tops.min()) / (ctx.tol() + 1e-12) / 2,
        p,
    )


def _head_shoulders(
    pv: list[Pivot], ctx: _Context
) -> tuple[float, float, float, list[Pivot]] | None:
    p = pv[-5:]
    if len(p) < 5 or _kinds(p) != "HLHLH" or not _spaced(p, ctx.settings.min_spacing):
        return None
    ls, l2, head, l4, rs = (x.price for x in p)
    tol = ctx.tol()
    if head < max(ls, rs) + tol / 2 or abs(ls - rs) > 1.5 * tol or abs(l2 - l4) > 2 * tol:
        return None
    if min(ls, rs) - max(l2, l4) < ctx.height() / 2 or head - max(l2, l4) < ctx.height():
        return None
    slope = (l4 - l2) / max(p[3].index - p[1].index, 1)
    neck_at_end = l4 + slope * (p[4].index - p[3].index)
    neck_at_head = l2 + slope * (p[2].index - p[1].index)
    confidence = 1 - abs(ls - rs) / (1.5 * tol + 1e-12) / 2
    return neck_at_end, neck_at_end - (head - neck_at_head), confidence, p


def _lines(p: list[Pivot]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    highs = [x for x in p if x.kind == "high"]
    lows = [x for x in p if x.kind == "low"]
    hx = np.array([x.index for x in highs], dtype=float)
    hy = np.array([x.price for x in highs])
    lx = np.array([x.index for x in lows], dtype=float)
    ly = np.array([x.price for x in lows])
    return (
        np.polyfit(hx, hy, 1),
        np.polyfit(lx, ly, 1),
        np.concatenate([hx, lx]),
        np.concatenate(
            [hy - np.polyval(np.polyfit(hx, hy, 1), hx), ly - np.polyval(np.polyfit(lx, ly, 1), lx)]
        ),
    )


def _converging_family(
    pv: list[Pivot], ctx: _Context
) -> list[tuple[str, int, float, float, float, list[Pivot]]]:
    """Triangles, wedges, rectangles, channels and broadening formations on the last 5-6 pivots."""
    found: list[tuple[str, int, float, float, float, list[Pivot]]] = []
    for n in (6, 5):
        p = pv[-n:]
        if len(p) < n or not _spaced(p, ctx.settings.min_spacing):
            continue
        high_line, low_line, _, residuals = _lines(p)
        if np.abs(residuals).max() > 1.5 * ctx.tol():
            continue
        x0, x1 = float(p[0].index), float(p[-1].index)
        span = x1 - x0
        dh = high_line[0] * span / ctx.atr
        dl = low_line[0] * span / ctx.atr
        w0 = np.polyval(high_line, x0) - np.polyval(low_line, x0)
        w1 = np.polyval(high_line, x1) - np.polyval(low_line, x1)
        if max(w0, w1) < ctx.height():
            continue
        flat = ctx.settings.flat_tolerance
        upper, lower = float(np.polyval(high_line, x1)), float(np.polyval(low_line, x1))
        height = float(max(w0, w1))
        fit = 1 - float(np.abs(residuals).mean()) / (1.5 * ctx.tol() + 1e-12)
        converging = w1 < 0.75 * w0
        h_flat, l_flat = abs(dh) <= flat, abs(dl) <= flat
        if h_flat and dl > flat and converging:
            found.append(("ascending_triangle", 1, upper, upper + height, fit, p))
        elif l_flat and dh < -flat and converging:
            found.append(("descending_triangle", -1, lower, lower - height, fit, p))
        elif dh < -flat and dl > flat and converging:
            found.append(("symmetrical_triangle", 0, upper, upper + height, fit, p))
        elif dh > flat and dl > dh and converging:
            found.append(("rising_wedge", -1, lower, lower - height, fit, p))
        elif dl < -flat and dh < dl and converging:
            found.append(("falling_wedge", 1, upper, upper + height, fit, p))
        elif dh > flat and dl < -flat and w1 > 1.3 * w0:
            found.append(("broadening", 0, upper, upper + height, fit, p))
        elif h_flat and l_flat and abs(w1 - w0) < 0.3 * w0:
            found.append(("rectangle", 0, upper, upper + height, fit, p))
        elif dh > flat and dl > flat and abs(w1 - w0) < 0.35 * w0:
            found.append(("channel_up", 1, lower, upper + height, fit, p))
        elif dh < -flat and dl < -flat and abs(w1 - w0) < 0.35 * w0:
            found.append(("channel_down", -1, upper, lower - height, fit, p))
        if found:
            break
    return found


def _roundness(close: np.ndarray, start: int, end: int) -> tuple[float, float]:
    """Quadratic fit of closes over ``[start, end]``: (curvature sign-adjusted R², bottom share)."""
    y = close[start : end + 1]
    if len(y) < 8:
        return 0.0, 0.0
    x = np.linspace(-1, 1, len(y))
    coeffs = np.polyfit(x, y, 2)
    fitted = np.polyval(coeffs, x)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(((y - fitted) ** 2).sum()) / ss_tot if ss_tot > 0 else 0.0
    lo, hi = y.min(), y.max()
    bottom_share = float((y <= lo + (hi - lo) / 3).mean()) if hi > lo else 0.0
    return (r2 if coeffs[0] > 0 else -r2), bottom_share


def _cup_handle(pv: list[Pivot], ctx: _Context) -> tuple[float, float, float, list[Pivot]] | None:
    p = pv[-4:]
    if len(p) < 4 or _kinds(p) != "HLHL":
        return None
    h0, l1, h2, l3 = p
    rim = max(h0.price, h2.price)
    depth = (h0.price + h2.price) / 2 - l1.price
    if abs(h0.price - h2.price) > 1.5 * ctx.tol() or depth < 1.5 * ctx.height():
        return None
    handle = h2.price - l3.price
    cup_bars, handle_bars = h2.index - h0.index, l3.index - h2.index
    if (
        handle > 0.5 * depth
        or l3.price < l1.price + 0.5 * depth
        or cup_bars < 3 * max(handle_bars, 1)
    ):
        return None
    r2, bottom_share = _roundness(ctx.close, h0.index, h2.index)
    if r2 < 0.5 or bottom_share < 0.2:
        return None
    return rim, rim + depth, r2, p


def _rounding(pv: list[Pivot], ctx: _Context) -> tuple[float, float, float, list[Pivot]] | None:
    p = pv[-3:]
    if len(p) < 3 or _kinds(p) != "HLH":
        return None
    h0, l1, h2 = p
    depth = min(h0.price, h2.price) - l1.price
    if depth < 1.5 * ctx.height() or h2.index - h0.index < 20:
        return None
    r2, bottom_share = _roundness(ctx.close, h0.index, h2.index)
    if r2 < 0.75 or bottom_share < 0.25:
        return None
    rim = max(h0.price, h2.price)
    return rim, rim + depth, r2, p


def _v_shape(pv: list[Pivot], ctx: _Context) -> tuple[float, float, float, list[Pivot]] | None:
    p = pv[-3:]
    if len(p) < 3 or _kinds(p) != "HLH":
        return None
    h0, l1, h2 = p
    fall, rise = h0.price - l1.price, h2.price - l1.price
    fast = l1.index - h0.index <= 12 and h2.index - l1.index <= 12
    if not fast or fall < 2 * ctx.height() or rise < 0.8 * fall:
        return None
    return h0.price, h0.price + fall, min(rise / fall, 1.0), p


_Rule = Callable[[list[Pivot], _Context], "tuple[float, float, float, list[Pivot]] | None"]

# (bearish name, bullish name, rule written for the bearish/top form). The bullish form runs the
# same rule on mirrored prices.
_MIRRORED_RULES: Final[tuple[tuple[str, str, _Rule], ...]] = (
    ("double_top", "double_bottom", _double_top),
    ("triple_top", "triple_bottom", _triple_top),
    ("head_and_shoulders", "inverse_head_and_shoulders", _head_shoulders),
)
# (bullish name, bearish name, rule written for the bullish/bottom form).
_MIRRORED_BULL_RULES: Final[tuple[tuple[str, str, _Rule], ...]] = (
    ("cup_and_handle", "inverse_cup_and_handle", _cup_handle),
    ("rounding_bottom", "rounding_top", _rounding),
    ("v_bottom", "v_top", _v_shape),
)
# A match of the key supersedes a match of each value ending on the same pivot.
_SUPERSEDES: Final[dict[str, tuple[str, ...]]] = {
    "triple_top": ("double_top",),
    "triple_bottom": ("double_bottom",),
    "head_and_shoulders": ("double_top", "triple_top"),
    "inverse_head_and_shoulders": ("double_bottom", "triple_bottom"),
    "cup_and_handle": ("rounding_bottom",),
    "inverse_cup_and_handle": ("rounding_top",),
}

CHART_PATTERNS: Final[dict[str, int]] = {
    "double_top": -1,
    "double_bottom": 1,
    "triple_top": -1,
    "triple_bottom": 1,
    "head_and_shoulders": -1,
    "inverse_head_and_shoulders": 1,
    "ascending_triangle": 1,
    "descending_triangle": -1,
    "symmetrical_triangle": 0,
    "rising_wedge": -1,
    "falling_wedge": 1,
    "broadening": 0,
    "rectangle": 0,
    "channel_up": 1,
    "channel_down": -1,
    "bull_flag": 1,
    "bear_flag": -1,
    "bull_pennant": 1,
    "bear_pennant": -1,
    "cup_and_handle": 1,
    "inverse_cup_and_handle": -1,
    "rounding_bottom": 1,
    "rounding_top": -1,
    "v_bottom": 1,
    "v_top": -1,
}
"""Every chart pattern name with its usual direction (+1 bullish, -1 bearish, 0 neutral)."""


def _pivot_patterns(pivots: list[Pivot], ctx: _Context) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    for bear_name, bull_name, rule in _MIRRORED_RULES:
        hit = rule(pivots, ctx)
        if hit:
            out.append(_make(bear_name, -1, hit[3], hit[0], hit[1], hit[2]))
        flipped = _Context(-ctx.close, ctx.atr, ctx.settings)
        hit = rule(_flip(pivots), flipped)
        if hit:
            out.append(_make(bull_name, 1, pivots[-len(hit[3]) :], -hit[0], -hit[1], hit[2]))
    for bull_name, bear_name, rule in _MIRRORED_BULL_RULES:
        hit = rule(pivots, ctx)
        if hit:
            out.append(_make(bull_name, 1, hit[3], hit[0], hit[1], hit[2]))
        flipped = _Context(-ctx.close, ctx.atr, ctx.settings)
        hit = rule(_flip(pivots), flipped)
        if hit:
            out.append(_make(bear_name, -1, pivots[-len(hit[3]) :], -hit[0], -hit[1], hit[2]))
    for name, direction, breakout, target, fit, used in _converging_family(pivots, ctx):
        out.append(_make(name, direction, used, breakout, target, fit))
    names = {p.name for p in out}
    dropped = {d for n in names for d in _SUPERSEDES.get(n, ())}
    return [p for p in out if p.name not in dropped]


def _flags(
    bars: pd.DataFrame, average_range: np.ndarray, settings: ChartSettings
) -> list[ChartPattern]:
    """Pole, tight consolidation, breakout close: flags (parallel) and pennants (converging)."""
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    found: list[ChartPattern] = []
    last_end = -1
    for t in range(1, len(close)):
        a = average_range[t]
        if not np.isfinite(a) or a <= 0 or t <= last_end:
            continue
        hit: ChartPattern | None = None
        for flag_len in settings.flag_lengths:
            c0 = t - flag_len
            if c0 < 1:
                break
            seg_h, seg_l = high[c0:t], low[c0:t]
            for pole_len in settings.pole_lengths:
                p0 = c0 - pole_len
                if p0 < 0:
                    break
                pole = close[c0 - 1] - close[p0]
                if abs(pole) < settings.pole_atr * a:
                    continue
                direction = 1 if pole > 0 else -1
                consolidation = seg_h.max() - seg_l.min()
                if consolidation > 0.5 * abs(pole):
                    continue
                broke = close[t] > seg_h.max() if direction > 0 else close[t] < seg_l.min()
                if not broke:
                    continue
                x = np.arange(flag_len, dtype=float)
                hs, ls = np.polyfit(x, seg_h, 1)[0], np.polyfit(x, seg_l, 1)[0]
                width0 = seg_h[: max(flag_len // 3, 1)].max() - seg_l[: max(flag_len // 3, 1)].min()
                width1 = (
                    seg_h[-max(flag_len // 3, 1) :].max() - seg_l[-max(flag_len // 3, 1) :].min()
                )
                if hs < 0 < ls and width1 < 0.7 * width0:
                    kind = "pennant"
                elif direction * hs <= 0.1 * a and direction * ls <= 0.1 * a:
                    kind = "flag"
                else:
                    continue
                name = f"{'bull' if direction > 0 else 'bear'}_{kind}"
                level = seg_h.max() if direction > 0 else seg_l.min()
                hit = ChartPattern(
                    name=name,
                    direction=direction,
                    start=p0,
                    end=t,
                    detected_at=t,
                    breakout=float(level),
                    target=float(level + pole),
                    confidence=float(np.clip(1 - consolidation / abs(pole), 0, 1)),
                )
                break
            if hit:
                break
        if hit:
            found.append(hit)
            last_end = t + settings.hold
    return found


def detect_chart_patterns(
    bars: pd.DataFrame, settings: ChartSettings | None = None
) -> list[ChartPattern]:
    """Find chart patterns in OHLC bars, each stamped with the bar it became visible.

    Args:
        bars: Bars with ``high``, ``low`` and ``close`` (oldest first).
        settings: Detection thresholds.

    Returns:
        Patterns sorted by ``detected_at``.

    Raises:
        ValueError: When a required column is missing.
    """
    missing = [c for c in ("high", "low", "close") if c not in bars.columns]
    if missing:
        raise ValueError(f"chart patterns need columns {missing}")
    settings = settings or ChartSettings()
    average_range = atr(bars, settings.atr_period).to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    pivots = zigzag_pivots(bars, settings.zigzag_atr, settings.atr_period)
    found: list[ChartPattern] = []
    for k in range(len(pivots)):
        known = pivots[: k + 1]
        a = average_range[known[-1].confirmed]
        if not np.isfinite(a) or a <= 0:
            continue
        found.extend(_pivot_patterns(known, _Context(close, float(a), settings)))
    found.extend(_flags(bars, average_range, settings))
    return sorted(found, key=lambda p: (p.detected_at, p.name))


def patterns_frame(patterns: list[ChartPattern], index: pd.Index | None = None) -> pd.DataFrame:
    """Detected patterns as a table (one row per pattern)."""
    columns = [*ChartPattern.__dataclass_fields__]
    if not patterns:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([p.to_dict(index) for p in patterns])


def chart_pattern_frame(bars: pd.DataFrame, settings: ChartSettings | None = None) -> pd.DataFrame:
    """Per-bar chart pattern features without look-ahead.

    Each pattern gets a column holding its direction (neutral patterns use +1) for ``hold`` bars
    from the bar it was detected, and 0 otherwise.
    """
    settings = settings or ChartSettings()
    out = pd.DataFrame(0.0, index=bars.index, columns=[f"chp_{n}" for n in CHART_PATTERNS])
    values = out.to_numpy()
    for pattern in detect_chart_patterns(bars, settings):
        col = out.columns.get_loc(f"chp_{pattern.name}")
        stop = min(pattern.detected_at + settings.hold, len(out))
        values[pattern.detected_at : stop, col] = pattern.direction or 1
    return pd.DataFrame(values, index=bars.index, columns=out.columns)
