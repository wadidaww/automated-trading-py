"""Feature library shared by live trading, training and research.

Two entry points:

- ``window_features`` computes the live ``DataStage`` features from one rolling price window. The
  live pipeline calls it tick by tick; ``live_feature_frame`` replays it over a bar history so a
  model trained offline sees exactly the features it will see live.
- ``build_features`` computes vectorised feature groups over one symbol's OHLCV bars for research.

Every feature at row ``t`` uses only bars ``<= t``. Ratios and returns are used instead of price
levels so features are comparable across symbols and time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
import pandas as pd

from trader.data import indicators
from trader.utils.maths import mean, std_dev, z_score

# Fewer points than this and the window is too thin for any live feature.
MIN_WINDOW_POINTS: Final = 5
# Previous prices kept for the acceleration feature.
ACCELERATION_HISTORY: Final = 10
_OHLCV: Final = ("open", "high", "low", "close", "volume")

LIVE_FEATURES: Final[tuple[str, ...]] = (
    "z_score",
    "momentum",
    "trend_strength",
    "volatility",
    "rsi",
    "bb_position",
    "price_acceleration",
)


def window_features(prices: Sequence[float], previous: Sequence[float]) -> dict[str, float]:
    """Live features for the last price in ``prices`` (oldest first).

    Args:
        prices: Rolling price window ending with the current price.
        previous: Prices seen before the current one, oldest first (for acceleration).

    Returns:
        Feature name to value, keyed by ``LIVE_FEATURES``; neutral values while warming up.
    """
    price = prices[-1]
    if len(prices) < MIN_WINDOW_POINTS:
        return {
            "z_score": 0.0,
            "momentum": 0.0,
            "trend_strength": 0.0,
            "volatility": 0.0,
            "rsi": 50.0,
            "bb_position": 0.5,
            "price_acceleration": 0.0,
        }
    centre = mean(prices)
    spread = std_dev(prices)
    return {
        "z_score": z_score(price, centre, spread) if spread > 0 else 0.0,
        "momentum": indicators.momentum(prices),
        "trend_strength": indicators.trend_strength(prices),
        "volatility": indicators.volatility(prices),
        "rsi": indicators.rsi(prices),
        "bb_position": indicators.bollinger_position(prices, centre, spread),
        "price_acceleration": indicators.acceleration(previous, price),
    }


def live_feature_frame(close: pd.Series, window: int = 100) -> pd.DataFrame:
    """Replay ``window_features`` over a close series, as ``DataStage`` would see it bar by bar.

    Rows before the window holds ``MIN_WINDOW_POINTS`` prices are NaN (warm-up).
    """
    values = close.astype(float).to_numpy()
    rows: list[dict[str, float]] = []
    for end in range(len(values)):
        prices = values[max(0, end - window + 1) : end + 1].tolist()
        # DataStage only records prices once the window is warm, so mirror that here.
        first_recorded = MIN_WINDOW_POINTS - 1
        previous = values[max(first_recorded, end - ACCELERATION_HISTORY) : end].tolist()
        if len(prices) < MIN_WINDOW_POINTS:
            rows.append(dict.fromkeys(LIVE_FEATURES, np.nan))
        else:
            rows.append(window_features(prices, previous))
    return pd.DataFrame(rows, index=close.index, columns=list(LIVE_FEATURES))


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """Which feature groups to build and with which lookback windows (in bars)."""

    groups: tuple[str, ...] = ("momentum", "trend", "volatility", "volume")
    windows: tuple[int, ...] = (5, 20, 60)
    live_window: int = 100

    @property
    def key(self) -> str:
        """Stable short identifier used to cache and log feature sets."""
        groups = "+".join(self.groups)
        windows = "-".join(str(w) for w in self.windows)
        return f"{groups}|w{windows}|lw{self.live_window}"


def _require(frame: pd.DataFrame, columns: Sequence[str], group: str) -> None:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise ValueError(f"feature group '{group}' needs columns {missing}")


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, np.nan)


def _momentum(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    close = bars["close"]
    out = {f"ret_{w}": close.pct_change(w, fill_method=None) for w in spec.windows}
    # Momentum that skips the most recent window, so it does not overlap short-term reversal.
    if len(spec.windows) >= 2:
        short, long = min(spec.windows), max(spec.windows)
        out[f"mom_{long}_{short}"] = close.shift(short) / close.shift(long) - 1
    return pd.DataFrame(out)


def _reversal(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    _require(bars, ["open"], "reversal")
    close = bars["close"]
    return pd.DataFrame(
        {
            "ret_1": close.pct_change(fill_method=None),
            "overnight_gap": bars["open"] / close.shift(1) - 1,
            "intraday_ret": bars["close"] / bars["open"] - 1,
        }
    )


def _trend(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    close = bars["close"]
    out = {f"ema_gap_{w}": close / _ema(close, w) - 1 for w in spec.windows}
    macd = _ema(close, 12) - _ema(close, 26)
    out["macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / close
    for w in spec.windows:
        x = np.arange(w, dtype=float)
        x_centred = x - x.mean()
        denom = float((x_centred**2).sum())

        def slope(window: np.ndarray, xc: np.ndarray = x_centred, d: float = denom) -> float:
            return float((xc * (window - window.mean())).sum() / d)

        out[f"trend_slope_{w}"] = np.log(close).rolling(w).apply(slope, raw=True)
    if {"high", "low"} <= set(bars.columns):
        out["adx_14"] = _adx(bars, 14)
    return pd.DataFrame(out)


def _adx(bars: pd.DataFrame, period: int) -> pd.Series:
    high, low = bars["high"], bars["low"]
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = _true_range(bars)
    alpha = 1 / period
    atr = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = 100 * _safe_div(plus_dm.ewm(alpha=alpha, adjust=False).mean(), atr)
    minus_di = 100 * _safe_div(minus_dm.ewm(alpha=alpha, adjust=False).mean(), atr)
    dx = 100 * _safe_div((plus_di - minus_di).abs(), plus_di + minus_di)
    return dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()


def _true_range(bars: pd.DataFrame) -> pd.Series:
    prev_close = bars["close"].shift(1)
    return pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _oscillators(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    close = bars["close"]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out = {"rsi_14": 100 - 100 / (1 + _safe_div(gain, loss))}
    high = bars["high"] if "high" in bars.columns else close
    low = bars["low"] if "low" in bars.columns else close
    for w in spec.windows:
        hh = high.rolling(w).max()
        ll = low.rolling(w).min()
        out[f"stoch_{w}"] = _safe_div(close - ll, hh - ll)
        out[f"dist_high_{w}"] = close / hh - 1
        out[f"dist_low_{w}"] = close / ll - 1
    mid = close.rolling(20).mean()
    out["boll_z_20"] = _safe_div(close - mid, close.rolling(20).std())
    return pd.DataFrame(out)


def _volatility(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    log_ret = np.log(bars["close"]).diff()
    out = {f"rv_{w}": log_ret.rolling(w).std() for w in spec.windows}
    if {"high", "low"} <= set(bars.columns):
        hl = np.log(bars["high"] / bars["low"]) ** 2
        for w in spec.windows:
            out[f"parkinson_{w}"] = np.sqrt(hl.rolling(w).mean() / (4 * np.log(2)))
        out["atr_14_pct"] = (
            _true_range(bars).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / bars["close"]
        )
    if len(spec.windows) >= 2:
        short, long = min(spec.windows), max(spec.windows)
        out[f"rv_ratio_{short}_{long}"] = _safe_div(out[f"rv_{short}"], out[f"rv_{long}"])
    return pd.DataFrame(out)


def _volume(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    _require(bars, ["volume"], "volume")
    close, volume = bars["close"], bars["volume"].astype(float)
    ret = close.pct_change(fill_method=None)
    dollar = close * volume
    obv = (np.sign(ret.fillna(0.0)) * volume).cumsum()
    out: dict[str, pd.Series] = {"log_dollar_volume": np.log1p(dollar)}
    for w in spec.windows:
        out[f"volume_ratio_{w}"] = _safe_div(volume, volume.rolling(w).mean())
        out[f"amihud_{w}"] = (ret.abs() / dollar.replace(0, np.nan)).rolling(w).mean() * 1e9
        out[f"obv_flow_{w}"] = _safe_div(obv - obv.shift(w), volume.rolling(w).sum())
    return pd.DataFrame(out)


def _live(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    return live_feature_frame(bars["close"], spec.live_window)


def _candles(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    _require(bars, ["open", "high", "low"], "candles")
    o, h, low, c = bars["open"], bars["high"], bars["low"], bars["close"]
    span = h - low
    out = {
        "body_ratio": _safe_div(c - o, span),
        "upper_wick": _safe_div(h - np.maximum(o, c), span),
        "lower_wick": _safe_div(np.minimum(o, c) - low, span),
        # Close location value: +1 closes on the high, -1 on the low.
        "clv": _safe_div((c - low) - (h - c), span),
        "range_pct": span / c,
    }
    for w in spec.windows:
        out[f"clv_mean_{w}"] = out["clv"].rolling(w).mean()
    return pd.DataFrame(out)


def _channels(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    close = bars["close"]
    high = bars["high"] if "high" in bars.columns else close
    low = bars["low"] if "low" in bars.columns else close
    out: dict[str, pd.Series] = {}
    for w in spec.windows:
        hh, ll = high.rolling(w).max(), low.rolling(w).min()
        out[f"donchian_pos_{w}"] = _safe_div(close - ll, hh - ll) * 2 - 1
        mid, sd = close.rolling(w).mean(), close.rolling(w).std()
        out[f"bb_pctb_{w}"] = _safe_div(close - (mid - 2 * sd), 4 * sd)
        out[f"bb_width_{w}"] = _safe_div(4 * sd, mid)
        if {"high", "low"} <= set(bars.columns):
            atr = _true_range(bars).ewm(span=w, adjust=False, min_periods=w).mean()
            out[f"keltner_pos_{w}"] = _safe_div(close - _ema(close, w), 2 * atr)
    return pd.DataFrame(out)


def _money_flow(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    _require(bars, ["high", "low", "volume"], "money_flow")
    high, low, close = bars["high"], bars["low"], bars["close"]
    volume = bars["volume"].astype(float)
    typical = (high + low + close) / 3
    flow = typical * volume
    up = flow.where(typical.diff() > 0, 0.0)
    down = flow.where(typical.diff() < 0, 0.0)
    clv = _safe_div((close - low) - (high - close), high - low).fillna(0.0)
    out: dict[str, pd.Series] = {}
    for w in spec.windows:
        out[f"mfi_{w}"] = 100 * _safe_div(up.rolling(w).sum(), (up + down).rolling(w).sum())
        out[f"cmf_{w}"] = _safe_div((clv * volume).rolling(w).sum(), volume.rolling(w).sum())
        vwap = _safe_div(flow.rolling(w).sum(), volume.rolling(w).sum())
        out[f"vwap_gap_{w}"] = close / vwap - 1
        force = close.diff() * volume
        out[f"force_{w}"] = _safe_div(_ema(force, w), _ema(close * volume, w))
    tp_mean = typical.rolling(20).mean()
    mean_dev = (typical - tp_mean).abs().rolling(20).mean()
    out["cci_20"] = _safe_div(typical - tp_mean, 0.015 * mean_dev)
    return pd.DataFrame(out)


def _stats(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    log_ret = np.log(bars["close"]).diff()
    close = bars["close"]
    out: dict[str, pd.Series] = {}
    for w in spec.windows:
        if w < 5:
            continue
        out[f"skew_{w}"] = log_ret.rolling(w).skew()
        out[f"kurt_{w}"] = log_ret.rolling(w).kurt()
        out[f"autocorr_{w}"] = log_ret.rolling(w).corr(log_ret.shift(1))
        out[f"zscore_{w}"] = _safe_div(close - close.rolling(w).mean(), close.rolling(w).std())
        # Variance ratio: > 1 trending, < 1 mean-reverting over the window.
        two_bar = np.log(close).diff(2)
        out[f"var_ratio_{w}"] = _safe_div(two_bar.rolling(w).var(), 2 * log_ret.rolling(w).var())
    return pd.DataFrame(out)


def _regime(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    close = bars["close"]
    log_ret = np.log(close).diff()
    vol_20 = log_ret.rolling(20).std()
    year = 252
    return pd.DataFrame(
        {
            "vol_pct_rank": vol_20.rolling(year, min_periods=60).rank(pct=True),
            "drawdown_252": close / close.rolling(year, min_periods=20).max() - 1,
            "above_ema_200": (close > close.ewm(span=200, adjust=False).mean()).astype(float),
            "ema_200_gap": close / close.ewm(span=200, adjust=False, min_periods=60).mean() - 1,
        }
    )


def _calendar(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    index = pd.DatetimeIndex(bars.index)
    dow = index.dayofweek.to_numpy(dtype=float)
    month = index.month.to_numpy(dtype=float)
    month_end = (index + pd.offsets.BDay(1)).month != index.month
    return pd.DataFrame(
        {
            "dow_sin": np.sin(2 * np.pi * dow / 5),
            "dow_cos": np.cos(2 * np.pi * dow / 5),
            "month_sin": np.sin(2 * np.pi * month / 12),
            "month_cos": np.cos(2 * np.pi * month / 12),
            "month_end": month_end.astype(float),
        },
        index=bars.index,
    )


FEATURE_GROUPS: Final[dict[str, Callable[[pd.DataFrame, FeatureSpec], pd.DataFrame]]] = {
    "momentum": _momentum,
    "reversal": _reversal,
    "trend": _trend,
    "oscillators": _oscillators,
    "volatility": _volatility,
    "volume": _volume,
    "candles": _candles,
    "channels": _channels,
    "money_flow": _money_flow,
    "stats": _stats,
    "regime": _regime,
    "calendar": _calendar,
    "live": _live,
}

# Groups computed across symbols after the per-symbol groups (see ``research.dataset``).
CROSS_SECTIONAL_GROUPS: Final[tuple[str, ...]] = ("market",)


def build_features(bars: pd.DataFrame, spec: FeatureSpec | None = None) -> pd.DataFrame:
    """Compute the feature groups in ``spec`` over one symbol's bars (oldest first).

    Args:
        bars: OHLCV bars with at least a ``close`` column; ``open``/``high``/``low``/``volume``
            unlock more features.
        spec: Groups and windows to build; defaults to ``FeatureSpec()``.

    Returns:
        One row per bar, same index as ``bars``; warm-up rows contain NaN.

    Raises:
        ValueError: On an unknown group or a group whose input columns are missing.

    The cross-sectional ``market`` group is skipped here; ``research.dataset`` adds it.
    """
    spec = spec or FeatureSpec()
    _require(bars, ["close"], "any")
    known = set(FEATURE_GROUPS) | set(CROSS_SECTIONAL_GROUPS)
    unknown = [g for g in spec.groups if g not in known]
    if unknown:
        raise ValueError(f"unknown feature groups {unknown}; known: {sorted(known)}")
    numeric = bars.astype({c: float for c in bars.columns if c in _OHLCV})
    per_symbol = [g for g in spec.groups if g in FEATURE_GROUPS]
    if not per_symbol:
        return pd.DataFrame(index=bars.index)
    frames = [FEATURE_GROUPS[group](numeric, spec) for group in per_symbol]
    out = pd.concat(frames, axis=1)
    out = out.loc[:, ~out.columns.duplicated()]
    return out.replace([np.inf, -np.inf], np.nan)
