"""Extended technical indicator library for research feature groups.

Every function here takes OHLCV bars (oldest first) and returns series indexed like the bars.
Each value at row ``t`` uses only bars ``<= t``. The ``*_group`` functions at the bottom turn the
indicators into scale-free features (ratios, oscillators, ATR units) and are registered as
feature groups in ``trader.data.features``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Final

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from trader.data.features import FeatureSpec


# --- Building blocks --------------------------------------------------------------------------


def safe_div(numerator: pd.Series, denominator: pd.Series | float) -> pd.Series:
    """Division that maps a zero denominator to NaN instead of inf."""
    if isinstance(denominator, pd.Series):
        return numerator / denominator.replace(0, np.nan)
    return numerator / denominator if denominator else numerator * np.nan


def sma(series: pd.Series, n: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    """Exponential moving average with span ``n``."""
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(series: pd.Series, n: int) -> pd.Series:
    """Wilder's moving average (alpha = 1/n), as used by RSI, ATR and ADX."""
    return series.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def wma(series: pd.Series, n: int) -> pd.Series:
    """Linearly weighted moving average (latest bar weighted ``n``)."""
    weights = np.arange(1, n + 1, dtype=float)
    return series.rolling(n).apply(lambda w: float(w @ weights) / weights.sum(), raw=True)


def dema(series: pd.Series, n: int) -> pd.Series:
    """Double exponential moving average."""
    e1 = ema(series, n)
    return 2 * e1 - ema(e1, n)


def tema(series: pd.Series, n: int) -> pd.Series:
    """Triple exponential moving average."""
    e1 = ema(series, n)
    e2 = ema(e1, n)
    return 3 * e1 - 3 * e2 + ema(e2, n)


def hma(series: pd.Series, n: int) -> pd.Series:
    """Hull moving average."""
    half = max(n // 2, 1)
    return wma(2 * wma(series, half) - wma(series, n), max(int(np.sqrt(n)), 1))


def zlema(series: pd.Series, n: int) -> pd.Series:
    """Zero-lag EMA: an EMA of the price plus its own momentum over the lag."""
    lag = (n - 1) // 2
    return ema(series + (series - series.shift(lag)), n)


def t3(series: pd.Series, n: int, v: float = 0.7) -> pd.Series:
    """Tillson T3 moving average (six chained EMAs)."""
    e1 = ema(series, n)
    e2 = ema(e1, n)
    e3 = ema(e2, n)
    e4 = ema(e3, n)
    e5 = ema(e4, n)
    e6 = ema(e5, n)
    c1 = -(v**3)
    c2 = 3 * v**2 + 3 * v**3
    c3 = -6 * v**2 - 3 * v - 3 * v**3
    c4 = 1 + 3 * v + v**3 + 3 * v**2
    return c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3


def alma(series: pd.Series, n: int, offset: float = 0.85, sigma: float = 6.0) -> pd.Series:
    """Arnaud Legoux moving average (Gaussian-weighted)."""
    m = offset * (n - 1)
    s = n / sigma
    weights = np.exp(-((np.arange(n) - m) ** 2) / (2 * s * s))
    weights /= weights.sum()
    return series.rolling(n).apply(lambda w: float(w @ weights), raw=True)


def kama(series: pd.Series, n: int = 10, fast: int = 2, slow: int = 30) -> pd.Series:
    """Kaufman adaptive moving average."""
    values = series.to_numpy(dtype=float)
    change = np.abs(series - series.shift(n)).to_numpy()
    noise = series.diff().abs().rolling(n).sum().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        er = np.where(noise > 0, change / noise, 0.0)
    fast_sc, slow_sc = 2 / (fast + 1), 2 / (slow + 1)
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
    out = np.full(len(values), np.nan)
    for i in range(n, len(values)):
        if np.isnan(out[i - 1]):
            out[i] = values[i]
        else:
            out[i] = out[i - 1] + sc[i] * (values[i] - out[i - 1])
    return pd.Series(out, index=series.index)


def vwma(close: pd.Series, volume: pd.Series, n: int) -> pd.Series:
    """Volume-weighted moving average."""
    return safe_div((close * volume).rolling(n).sum(), volume.rolling(n).sum())


def true_range(bars: pd.DataFrame) -> pd.Series:
    """True range: the bar's range extended to the previous close."""
    prev_close = bars["close"].shift(1)
    return pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average true range (Wilder)."""
    return rma(true_range(bars), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative strength index (Wilder), 0..100."""
    delta = close.diff()
    gain = rma(delta.clip(lower=0), n)
    loss = rma(-delta.clip(upper=0), n)
    value = 100 - 100 / (1 + safe_div(gain, loss))
    # No losses in the window: 100 if there were gains, 50 if price did not move at all.
    flat = pd.Series(np.where(gain > 0, 100.0, 50.0), index=close.index)
    return value.where(loss != 0, flat).where(loss.notna())


def stochastic(bars: pd.DataFrame, n: int = 14, smooth: int = 3) -> tuple[pd.Series, pd.Series]:
    """Slow stochastic %K and %D, 0..100."""
    hh, ll = bars["high"].rolling(n).max(), bars["low"].rolling(n).min()
    fast_k = 100 * safe_div(bars["close"] - ll, hh - ll)
    k = sma(fast_k, smooth)
    return k, sma(k, smooth)


def directional_indicators(
    bars: pd.DataFrame, n: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """+DI, -DI and ADX."""
    up = bars["high"].diff()
    down = -bars["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    average_range = atr(bars, n)
    plus_di = 100 * safe_div(rma(plus_dm, n), average_range)
    minus_di = 100 * safe_div(rma(minus_dm, n), average_range)
    dx = 100 * safe_div((plus_di - minus_di).abs(), plus_di + minus_di)
    return plus_di, minus_di, rma(dx, n)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series]:
    """MACD line and signal line."""
    line = ema(close, fast) - ema(close, slow)
    return line, line.ewm(span=signal, adjust=False).mean()


def parabolic_sar(
    bars: pd.DataFrame, step: float = 0.02, max_step: float = 0.2
) -> tuple[pd.Series, pd.Series]:
    """Parabolic SAR level and trend direction (+1 up, -1 down)."""
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    n = len(high)
    sar = np.full(n, np.nan)
    direction = np.zeros(n)
    if n < 2:
        return pd.Series(sar, index=bars.index), pd.Series(direction, index=bars.index)
    up = high[1] >= high[0]
    extreme = high[1] if up else low[1]
    level = low[0] if up else high[0]
    accel = step
    for i in range(1, n):
        level = level + accel * (extreme - level)
        if up:
            level = min(level, low[i - 1], low[max(i - 2, 0)])
            if low[i] < level:
                up, level, extreme, accel = False, extreme, low[i], step
            elif high[i] > extreme:
                extreme, accel = high[i], min(accel + step, max_step)
        else:
            level = max(level, high[i - 1], high[max(i - 2, 0)])
            if high[i] > level:
                up, level, extreme, accel = True, extreme, high[i], step
            elif low[i] < extreme:
                extreme, accel = low[i], min(accel + step, max_step)
        sar[i] = level
        direction[i] = 1.0 if up else -1.0
    return pd.Series(sar, index=bars.index), pd.Series(direction, index=bars.index)


def supertrend(
    bars: pd.DataFrame, n: int = 10, multiplier: float = 3.0
) -> tuple[pd.Series, pd.Series]:
    """Supertrend line and direction (+1 up, -1 down)."""
    mid = ((bars["high"] + bars["low"]) / 2).to_numpy(dtype=float)
    band = (multiplier * atr(bars, n)).to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    length = len(close)
    upper = np.full(length, np.nan)
    lower = np.full(length, np.nan)
    line = np.full(length, np.nan)
    direction = np.zeros(length)
    trend = 1.0
    for i in range(length):
        if np.isnan(band[i]):
            continue
        basic_up, basic_low = mid[i] + band[i], mid[i] - band[i]
        prev_ok = i > 0 and not np.isnan(upper[i - 1])
        upper[i] = (
            basic_up
            if not prev_ok or basic_up < upper[i - 1] or close[i - 1] > upper[i - 1]
            else upper[i - 1]
        )
        lower[i] = (
            basic_low
            if not prev_ok or basic_low > lower[i - 1] or close[i - 1] < lower[i - 1]
            else lower[i - 1]
        )
        if prev_ok:
            if trend < 0 and close[i] > upper[i - 1]:
                trend = 1.0
            elif trend > 0 and close[i] < lower[i - 1]:
                trend = -1.0
        line[i] = lower[i] if trend > 0 else upper[i]
        direction[i] = trend
    return pd.Series(line, index=bars.index), pd.Series(direction, index=bars.index)


def heikin_ashi(bars: pd.DataFrame) -> pd.DataFrame:
    """Heikin-Ashi open, high, low and close."""
    ha_close = (bars["open"] + bars["high"] + bars["low"] + bars["close"]) / 4
    o = bars["open"].to_numpy(dtype=float)
    hc = ha_close.to_numpy(dtype=float)
    ha_open = np.empty(len(o))
    if len(o):
        ha_open[0] = (o[0] + bars["close"].iloc[0]) / 2
    for i in range(1, len(o)):
        ha_open[i] = (ha_open[i - 1] + hc[i - 1]) / 2
    ha_open_s = pd.Series(ha_open, index=bars.index)
    return pd.DataFrame(
        {
            "open": ha_open_s,
            "high": pd.concat([bars["high"], ha_open_s, ha_close], axis=1).max(axis=1),
            "low": pd.concat([bars["low"], ha_open_s, ha_close], axis=1).min(axis=1),
            "close": ha_close,
        }
    )


def _streak(condition: pd.Series) -> pd.Series:
    """Signed run length: +k after k bars of ``True``, -k after k bars of ``False``."""
    sign = np.where(condition.to_numpy(), 1.0, -1.0)
    out = np.zeros(len(sign))
    for i in range(len(sign)):
        out[i] = out[i - 1] + sign[i] if i and np.sign(out[i - 1]) == sign[i] else sign[i]
    return pd.Series(out, index=condition.index)


def _rolling_linreg(series: pd.Series, n: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Rolling least-squares slope, R² and end-of-window fitted value."""
    x = np.arange(n, dtype=float)
    xc = x - x.mean()
    sxx = float((xc**2).sum())
    mean_y = series.rolling(n).mean()
    sxy = series.rolling(n).apply(lambda w: float(xc @ (w - w.mean())), raw=True)
    slope = sxy / sxx
    var_y = series.rolling(n).var(ddof=0) * n
    r2 = safe_div(slope**2 * sxx, var_y)
    fitted_end = mean_y + slope * xc[-1]
    return slope, r2, fitted_end


_FIB_LEVELS: Final = np.array([0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0])


# --- Feature groups ---------------------------------------------------------------------------


def _ohlc(bars: pd.DataFrame, group: str) -> None:
    missing = [c for c in ("open", "high", "low") if c not in bars.columns]
    if missing:
        raise ValueError(f"feature group '{group}' needs columns {missing}")


def moving_averages_group(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    """Close relative to many moving-average families, plus the 50/200 cross."""
    close = bars["close"]
    out: dict[str, pd.Series] = {}
    for w in spec.windows:
        if w < 2:
            continue
        out[f"sma_gap_{w}"] = close / sma(close, w) - 1
        out[f"wma_gap_{w}"] = close / wma(close, w) - 1
        out[f"dema_gap_{w}"] = close / dema(close, w) - 1
        out[f"tema_gap_{w}"] = close / tema(close, w) - 1
        out[f"hma_gap_{w}"] = close / hma(close, w) - 1
        out[f"zlema_gap_{w}"] = close / zlema(close, w) - 1
        out[f"kama_gap_{w}"] = close / kama(close, w) - 1
        out[f"t3_gap_{w}"] = close / t3(close, w) - 1
        out[f"alma_gap_{w}"] = close / alma(close, w) - 1
        if "volume" in bars.columns:
            out[f"vwma_gap_{w}"] = close / vwma(close, bars["volume"], w) - 1
    sma50, sma200 = sma(close, 50), sma(close, 200)
    out["sma_50_200_gap"] = sma50 / sma200 - 1
    out["golden_cross"] = (sma50 > sma200).astype(float).where(sma200.notna())
    return pd.DataFrame(out, index=bars.index)


def momentum_ext_group(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    """Oscillators and momentum indicators beyond the base groups."""
    _ohlc(bars, "momentum_ext")
    o, h, low, c = bars["open"], bars["high"], bars["low"], bars["close"]
    out: dict[str, pd.Series] = {}
    for w in spec.windows:
        out[f"roc_{w}"] = c.pct_change(w, fill_method=None)
        delta = c.diff()
        up, down = delta.clip(lower=0).rolling(w).sum(), (-delta.clip(upper=0)).rolling(w).sum()
        out[f"cmo_{w}"] = 100 * safe_div(up - down, up + down)
        hh, ll = h.rolling(w).max(), low.rolling(w).min()
        out[f"williams_r_{w}"] = -100 * safe_div(hh - c, hh - ll)
        out[f"qstick_{w}"] = safe_div(sma(c - o, w), c)
    for n in (7, 21):
        out[f"rsi_{n}"] = rsi(c, n)
    k, d = stochastic(bars, 14, 3)
    out["stoch_k_14"], out["stoch_d_14"] = k, d
    out["stoch_kd_diff"] = k - d
    r = rsi(c, 14)
    rsi_ll, rsi_hh = r.rolling(14).min(), r.rolling(14).max()
    out["stoch_rsi_14"] = safe_div(r - rsi_ll, rsi_hh - rsi_ll)
    ppo_line = 100 * safe_div(ema(c, 12) - ema(c, 26), ema(c, 26))
    ppo_signal = ppo_line.ewm(span=9, adjust=False).mean()
    out["ppo"], out["ppo_hist"] = ppo_line, ppo_line - ppo_signal
    triple = ema(ema(ema(np.log(c), 15), 15), 15)
    out["trix_15"] = 1e4 * triple.diff()
    mom = c.diff()
    out["tsi"] = 100 * safe_div(ema(ema(mom, 25), 13), ema(ema(mom.abs(), 25), 13))
    prev_c = c.shift(1)
    buying = c - pd.concat([low, prev_c], axis=1).min(axis=1)
    tr = true_range(bars)
    avg = [safe_div(buying.rolling(n).sum(), tr.rolling(n).sum()) for n in (7, 14, 28)]
    out["ultimate_osc"] = 100 * (4 * avg[0] + 2 * avg[1] + avg[2]) / 7
    median = (h + low) / 2
    ao = sma(median, 5) - sma(median, 34)
    out["awesome_osc"] = safe_div(ao, c)
    out["accelerator_osc"] = safe_div(ao - sma(ao, 5), c)
    roc = {n: c.pct_change(n, fill_method=None) * 100 for n in (10, 15, 20, 30)}
    out["kst"] = (
        sma(roc[10], 10) + 2 * sma(roc[15], 10) + 3 * sma(roc[20], 10) + 4 * sma(roc[30], 15)
    )
    out["coppock"] = wma(
        c.pct_change(14, fill_method=None) + c.pct_change(11, fill_method=None), 10
    )
    streak = _streak(c.diff() > 0).where(c.diff() != 0, 0.0)
    pct_rank = c.pct_change(fill_method=None).rolling(100, min_periods=20).rank(pct=True) * 100
    out["connors_rsi"] = (rsi(c, 3) + rsi(streak, 2) + pct_rank) / 3
    num = (c - o) + 2 * (c - o).shift(1) + 2 * (c - o).shift(2) + (c - o).shift(3)
    den = (h - low) + 2 * (h - low).shift(1) + 2 * (h - low).shift(2) + (h - low).shift(3)
    rvgi = safe_div(num.rolling(10).sum(), den.rolling(10).sum())
    out["rvgi"] = rvgi
    out["rvgi_signal_gap"] = (
        rvgi - (rvgi + 2 * rvgi.shift(1) + 2 * rvgi.shift(2) + rvgi.shift(3)) / 6
    )
    hh10, ll10 = median.rolling(10).max(), median.rolling(10).min()
    value = (2 * safe_div(median - ll10, hh10 - ll10) - 1).clip(-0.999, 0.999)
    smoothed = value.ewm(alpha=0.33, adjust=False).mean().clip(-0.999, 0.999)
    out["fisher_10"] = 0.5 * np.log((1 + smoothed) / (1 - smoothed))
    # Detrended price oscillator, using only past bars (the centred version looks ahead).
    out["dpo_20"] = safe_div(c - sma(c, 20).shift(11), c)
    macd_line = ema(c, 23) - ema(c, 50)
    stoch1 = 100 * safe_div(
        macd_line - macd_line.rolling(10).min(),
        macd_line.rolling(10).max() - macd_line.rolling(10).min(),
    )
    pf = stoch1.ewm(alpha=0.5, adjust=False).mean()
    stoch2 = 100 * safe_div(pf - pf.rolling(10).min(), pf.rolling(10).max() - pf.rolling(10).min())
    out["schaff_trend_cycle"] = stoch2.ewm(alpha=0.5, adjust=False).mean()
    e13 = ema(c, 13)
    out["elder_bull_power"] = safe_div(h - e13, c)
    out["elder_bear_power"] = safe_div(low - e13, c)
    out["balance_of_power"] = sma(safe_div(c - o, h - low), 14)
    pmo = ema(10 * ema(c.pct_change(fill_method=None) * 100, 35), 20)
    out["pmo"] = pmo
    out["pmo_signal_gap"] = pmo - ema(pmo, 10)
    return pd.DataFrame(out, index=bars.index)


def trend_ext_group(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    """Trend direction and strength: Aroon, DMI, Vortex, SAR, Supertrend, Ichimoku and more."""
    _ohlc(bars, "trend_ext")
    h, low, c = bars["high"], bars["low"], bars["close"]
    out: dict[str, pd.Series] = {}
    n = 25
    since_high = h.rolling(n + 1).apply(lambda w: float(n - np.argmax(w)), raw=True)
    since_low = low.rolling(n + 1).apply(lambda w: float(n - np.argmin(w)), raw=True)
    out["aroon_up"] = 100 * (n - since_high) / n
    out["aroon_down"] = 100 * (n - since_low) / n
    out["aroon_osc"] = out["aroon_up"] - out["aroon_down"]
    plus_di, minus_di, adx = directional_indicators(bars, 14)
    out["plus_di_14"], out["minus_di_14"] = plus_di, minus_di
    out["di_spread_14"] = plus_di - minus_di
    out["adxr_14"] = (adx + adx.shift(14)) / 2
    tr = true_range(bars)
    vm_plus = (h - low.shift(1)).abs().rolling(14).sum()
    vm_minus = (low - h.shift(1)).abs().rolling(14).sum()
    out["vortex_plus"] = safe_div(vm_plus, tr.rolling(14).sum())
    out["vortex_minus"] = safe_div(vm_minus, tr.rolling(14).sum())
    average_range = atr(bars, 14)
    sar, sar_dir = parabolic_sar(bars)
    out["psar_dist_atr"] = safe_div(c - sar, average_range)
    out["psar_dir"] = sar_dir
    st_line, st_dir = supertrend(bars, 10, 3.0)
    out["supertrend_dist_atr"] = safe_div(c - st_line, average_range)
    out["supertrend_dir"] = st_dir
    tenkan = (h.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + low.rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((h.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    top = pd.concat([span_a, span_b], axis=1).max(axis=1, skipna=False)
    bottom = pd.concat([span_a, span_b], axis=1).min(axis=1, skipna=False)
    out["ichimoku_tenkan_gap"] = c / tenkan - 1
    out["ichimoku_kijun_gap"] = c / kijun - 1
    out["ichimoku_tk_cross"] = safe_div(tenkan - kijun, c)
    cloud_pos = np.where(c > top, 1.0, np.where(c < bottom, -1.0, 0.0))
    out["ichimoku_cloud_pos"] = pd.Series(cloud_pos, index=bars.index).where(top.notna())
    out["ichimoku_cloud_width"] = safe_div(top - bottom, c)
    out["ichimoku_chikou"] = c / c.shift(26) - 1
    hl = h - low
    ratio = safe_div(ema(hl, 9), ema(ema(hl, 9), 9))
    out["mass_index_25"] = ratio.rolling(25).sum()
    out["chop_14"] = (
        100
        * np.log10(safe_div(tr.rolling(14).sum(), h.rolling(14).max() - low.rolling(14).min()))
        / np.log10(14)
    )
    log_c = np.log(c)
    for w in spec.windows:
        if w < 3:
            continue
        slope, r2, fitted = _rolling_linreg(log_c, w)
        out[f"linreg_slope_{w}"] = slope
        out[f"linreg_r2_{w}"] = r2
        out[f"linreg_gap_{w}"] = log_c - fitted
        change = (c - c.shift(w)).abs()
        out[f"efficiency_ratio_{w}"] = safe_div(change, c.diff().abs().rolling(w).sum())
    ha = heikin_ashi(bars)
    ha_up = ha["close"] > ha["open"]
    out["heikin_ashi_streak"] = _streak(ha_up).clip(-10, 10)
    out["heikin_ashi_body"] = safe_div(ha["close"] - ha["open"], ha["high"] - ha["low"])
    line, signal = macd(c)
    out["macd_line"] = safe_div(line, c)
    out["macd_signal"] = safe_div(signal, c)
    return pd.DataFrame(out, index=bars.index)


def volatility_ext_group(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    """Range-based volatility estimators, squeeze, Ulcer index and relative volatility."""
    _ohlc(bars, "volatility_ext")
    o, h, low, c = bars["open"], bars["high"], bars["low"], bars["close"]
    log_hl = np.log(h / low)
    log_co = np.log(c / o)
    log_ho, log_lo = np.log(h / o), np.log(low / o)
    log_hc, log_lc = np.log(h / c), np.log(low / c)
    overnight = np.log(o / c.shift(1))
    gk = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    rs = log_ho * log_hc + log_lo * log_lc
    out: dict[str, pd.Series] = {}
    for w in spec.windows:
        if w < 2:
            continue
        out[f"garman_klass_{w}"] = np.sqrt(gk.rolling(w).mean().clip(lower=0))
        out[f"rogers_satchell_{w}"] = np.sqrt(rs.rolling(w).mean().clip(lower=0))
        k = 0.34 / (1.34 + (w + 1) / (w - 1))
        yz = (
            overnight.rolling(w).var()
            + k * log_co.rolling(w).var()
            + (1 - k) * rs.rolling(w).mean()
        )
        out[f"yang_zhang_{w}"] = np.sqrt(yz.clip(lower=0))
        hh, ll = h.rolling(w).max(), low.rolling(w).min()
        out[f"donchian_width_{w}"] = safe_div(hh - ll, c)
        rng = (h - low) / c
        out[f"range_z_{w}"] = safe_div(rng - rng.rolling(w).mean(), rng.rolling(w).std())
    drawdown = 100 * (c / c.rolling(14).max() - 1)
    out["ulcer_index_14"] = np.sqrt((drawdown**2).rolling(14).mean())
    average_range = atr(bars, 14)
    out["natr_14"] = 100 * average_range / c
    hl_ema = ema(h - low, 10)
    out["chaikin_volatility"] = hl_ema.pct_change(10, fill_method=None)
    mid, sd = sma(c, 20), c.rolling(20).std()
    kc_band = 1.5 * atr(bars, 20)
    squeeze = (mid + 2 * sd < mid + kc_band) & (mid - 2 * sd > mid - kc_band)
    out["bb_squeeze"] = squeeze.astype(float).where(sd.notna() & kc_band.notna())
    out["bb_kc_ratio"] = safe_div(2 * sd, kc_band)
    std10 = c.rolling(10).std()
    delta = c.diff()
    up = rma(std10.where(delta > 0, 0.0), 14)
    down = rma(std10.where(delta < 0, 0.0), 14)
    out["relative_vol_index"] = 100 * safe_div(up, up + down)
    log_ret = np.log(c).diff()
    out["vol_of_vol_20"] = log_ret.rolling(20).std().rolling(20).std()
    return pd.DataFrame(out, index=bars.index)


def volume_ext_group(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    """Accumulation/distribution, Chaikin, ease of movement, NVI/PVI, Klinger and more."""
    missing = [col for col in ("high", "low", "volume") if col not in bars.columns]
    if missing:
        raise ValueError(f"feature group 'volume_ext' needs columns {missing}")
    h, low, c = bars["high"], bars["low"], bars["close"]
    v = bars["volume"].astype(float)
    clv = safe_div((c - low) - (h - c), h - low).fillna(0.0)
    ad = (clv * v).cumsum()
    out: dict[str, pd.Series] = {}
    for w in spec.windows:
        out[f"ad_flow_{w}"] = safe_div(ad - ad.shift(w), v.rolling(w).sum())
        vol_mean, vol_std = v.rolling(w).mean(), v.rolling(w).std()
        out[f"volume_z_{w}"] = safe_div(v - vol_mean, vol_std)
        up_vol = v.where(c.diff() > 0, 0.0).rolling(w).sum()
        down_vol = v.where(c.diff() < 0, 0.0).rolling(w).sum()
        out[f"up_down_volume_{w}"] = safe_div(up_vol - down_vol, up_vol + down_vol)
    out["chaikin_osc"] = safe_div(ema(ad, 3) - ema(ad, 10), sma(v, 10))
    mid_move = ((h + low) / 2).diff()
    box = safe_div(v / 1e6, h - low)
    out["ease_of_movement_14"] = sma(safe_div(mid_move, box), 14) / c * 1e2
    ret = c.pct_change(fill_method=None).fillna(0.0)
    vol_down = v < v.shift(1)
    nvi = (1 + ret.where(vol_down, 0.0)).cumprod()
    pvi = (1 + ret.where(~vol_down, 0.0)).cumprod()
    out["nvi_gap"] = nvi / ema(nvi, 100) - 1
    out["pvi_gap"] = pvi / ema(pvi, 100) - 1
    pvt = (ret * v).cumsum()
    out["pvt_flow_20"] = safe_div(pvt - pvt.shift(20), sma(v, 20))
    typical = (h + low + c) / 3
    trend = np.sign(typical.diff()).fillna(0.0)
    dm = h - low
    vf = v * trend * dm
    out["klinger_osc"] = safe_div(ema(vf, 34) - ema(vf, 55), ema(v * dm, 34))
    out["pvo"] = 100 * safe_div(ema(v, 12) - ema(v, 26), ema(v, 26))
    out["elder_force_13"] = safe_div(ema(c.diff() * v, 13), ema(c * v, 13))
    return pd.DataFrame(out, index=bars.index)


def support_resistance_group(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    """Distance to classic and Fibonacci pivot levels and to recent swing highs and lows."""
    _ohlc(bars, "support_resistance")
    h, low, c = bars["high"], bars["low"], bars["close"]
    prev_h, prev_l, prev_c = h.shift(1), low.shift(1), c.shift(1)
    pivot = (prev_h + prev_l + prev_c) / 3
    span = prev_h - prev_l
    average_range = atr(bars, 14)
    levels = {
        "pivot": pivot,
        "r1": 2 * pivot - prev_l,
        "s1": 2 * pivot - prev_h,
        "r2": pivot + span,
        "s2": pivot - span,
        "fib_r1": pivot + 0.382 * span,
        "fib_s1": pivot - 0.382 * span,
        "cam_r3": prev_c + span * 1.1 / 4,
        "cam_s3": prev_c - span * 1.1 / 4,
    }
    out = {f"dist_{name}_atr": safe_div(c - level, average_range) for name, level in levels.items()}
    for w in spec.windows:
        if w < 2:
            continue
        hh, ll = h.rolling(w).max(), low.rolling(w).min()
        out[f"bars_since_high_{w}"] = (
            h.rolling(w).apply(lambda x: float(len(x) - 1 - np.argmax(x)), raw=True) / w
        )
        out[f"bars_since_low_{w}"] = (
            low.rolling(w).apply(lambda x: float(len(x) - 1 - np.argmin(x)), raw=True) / w
        )
        retrace = safe_div(hh - c, hh - ll)
        # Distance to the nearest Fibonacci retracement of the window's range.
        out[f"fib_nearest_{w}"] = retrace.apply(
            lambda r: float(np.min(np.abs(_FIB_LEVELS - r))) if np.isfinite(r) else np.nan
        )
        out[f"dist_high_atr_{w}"] = safe_div(c - hh, average_range)
        out[f"dist_low_atr_{w}"] = safe_div(c - ll, average_range)
    return pd.DataFrame(out, index=bars.index)


TECHNICAL_GROUPS: Final[dict[str, Callable[[pd.DataFrame, FeatureSpec], pd.DataFrame]]] = {
    "moving_averages": moving_averages_group,
    "momentum_ext": momentum_ext_group,
    "trend_ext": trend_ext_group,
    "volatility_ext": volatility_ext_group,
    "volume_ext": volume_ext_group,
    "support_resistance": support_resistance_group,
}
