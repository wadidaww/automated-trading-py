"""What happened after each pattern: forward returns by pattern name.

A pattern is only useful if price behaves differently after it. ``pattern_outcomes`` measures
the forward log return from the close of the detection bar, signed by the pattern's direction,
so a positive mean means the pattern "worked" on average. Neutral patterns are reported
unsigned. Compare ``mean_signed_return`` with the unconditional drift and with trading costs
before relying on a pattern.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trader.patterns.candlesticks import CANDLESTICK_PATTERNS, detect_candlestick_patterns
from trader.patterns.chart import ChartSettings, detect_chart_patterns


def _events(bars: pd.DataFrame, settings: ChartSettings) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for p in detect_chart_patterns(bars, settings):
        rows.append(
            {
                "pattern": p.name,
                "type": "chart",
                "position": p.detected_at,
                "direction": p.direction,
            }
        )
    candles = detect_candlestick_patterns(bars)
    for name in candles.columns:
        hits = np.flatnonzero(candles[name].to_numpy() != 0)
        neutral = CANDLESTICK_PATTERNS[name].kind == "neutral"
        for pos in hits:
            value = float(candles[name].iloc[pos])
            rows.append(
                {
                    "pattern": f"cdl_{name}"
                    + ("" if neutral else ("_bull" if value > 0 else "_bear")),
                    "type": "candlestick",
                    "position": int(pos),
                    "direction": 0 if neutral else int(np.sign(value)),
                }
            )
    return pd.DataFrame(rows, columns=["pattern", "type", "position", "direction"])


def pattern_outcomes(
    bars_by_symbol: dict[str, pd.DataFrame],
    horizon: int = 10,
    settings: ChartSettings | None = None,
) -> pd.DataFrame:
    """Forward-return statistics per pattern across symbols.

    Args:
        bars_by_symbol: OHLC bars per symbol.
        horizon: Bars ahead to measure the return over.
        settings: Chart detection settings.

    Returns:
        One row per pattern: ``count``, ``mean_signed_return``, ``hit_rate`` (share of signed
        returns > 0), ``t_stat`` and ``mean_abs_return``, sorted by ``t_stat``.
    """
    settings = settings or ChartSettings()
    parts: list[pd.DataFrame] = []
    for symbol, bars in bars_by_symbol.items():
        events = _events(bars, settings)
        if events.empty:
            continue
        log_close = np.log(bars["close"].to_numpy(dtype=float))
        pos = events["position"].to_numpy(dtype=int)
        ahead = pos + horizon
        valid = ahead < len(log_close)
        events = events[valid].copy()
        forward = log_close[ahead[valid]] - log_close[pos[valid]]
        sign = events["direction"].replace(0, 1).to_numpy(dtype=float)
        events["signed_return"] = forward * sign
        events["abs_return"] = np.abs(forward)
        events["symbol"] = symbol
        parts.append(events)
    columns = [
        "pattern",
        "type",
        "count",
        "mean_signed_return",
        "hit_rate",
        "t_stat",
        "mean_abs_return",
    ]
    if not parts:
        return pd.DataFrame(columns=columns)
    events = pd.concat(parts, ignore_index=True)
    grouped = events.groupby(["pattern", "type"])
    table = grouped.agg(
        count=("signed_return", "size"),
        mean_signed_return=("signed_return", "mean"),
        std=("signed_return", "std"),
        hit_rate=("signed_return", lambda r: float((r > 0).mean())),
        mean_abs_return=("abs_return", "mean"),
    ).reset_index()
    table["t_stat"] = table["mean_signed_return"] / (table["std"] / np.sqrt(table["count"]))
    return (
        table[columns]
        .sort_values("t_stat", ascending=False, na_position="last")
        .reset_index(drop=True)
    )
