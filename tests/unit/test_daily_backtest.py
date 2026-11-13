"""Daily-bar backtester: parity with a vectorised reference, costs, delay and guards."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trader.config import AppConfig, load_config
from trader.data.features import TrendSpec, live_feature_frame
from trader.model.sma_trend import SmaTrendModel
from trader.model.trend import TrendFollowingModel
from trader.research import daily_backtest as bt
from trader.research.synthetic import synthetic_bars
from trader.risk.pre_trade import OrderIntent, PreTradeRequest, Rejection, VolTargetSizing

from helpers import make_position
from test_pre_trade import SYMBOL, _Broker, _risk

SPEC = TrendSpec(lookbacks=(10,), donchian_entry=8, donchian_exit=4, vol_window=5, sma_window=20)
FULL = VolTargetSizing(target_vol_annual=2.0, symbol_weight=1.0, rebalance_band=0.10)


def _closes(n: int = 400, seed: int = 3) -> pd.Series:
    bars = synthetic_bars(n_bars=n, seed=seed)
    close = bars["close"].astype(float)
    close.index = pd.date_range("2020-01-01", periods=n, freq="D")
    return close


def _config(monkeypatch: pytest.MonkeyPatch, **trend: int) -> AppConfig:
    monkeypatch.setenv("FUTU_ACCOUNT_ID", "0")
    monkeypatch.setenv("FUTU_SECURITY_FIRM", "FUTUSECURITIES")
    payload = load_config("config/config.crypto.sma.yaml").model_dump()
    payload["model"]["trend"].update(trend)
    return AppConfig(**payload)


def test_targets_match_the_vectorised_rule() -> None:
    close = _closes()
    target = bt.target_weights(close, SmaTrendModel(), SPEC, FULL, window=60)
    above = close > close.rolling(SPEC.sma_window).mean()
    vol = close.pct_change().rolling(SPEC.vol_window).std(ddof=1) * math.sqrt(SPEC.bars_per_year)
    expected = (above * np.minimum(1.0, FULL.target_vol_annual / vol)).where(above, 0.0)
    warm = 60  # like the daily job, nothing is decided before the window is full
    assert target.iloc[: warm - 1].isna().all()
    assert target.iloc[warm - 1 :].notna().all()
    np.testing.assert_allclose(target.iloc[warm:], expected.iloc[warm:], rtol=1e-9)


def test_free_full_weight_run_equals_lagged_position_returns() -> None:
    close = _closes()
    target = bt.target_weights(close, SmaTrendModel(), SPEC, FULL, window=60)
    result = bt.simulate(close, target, FULL, initial_equity=1_000.0)
    # Decided on close t, filled on close t+1, so the position earns from bar t+2.
    held = (target.fillna(0.0) > 0).astype(float).shift(2).fillna(0.0)
    expected = 1_000.0 * (1 + held * close.pct_change().fillna(0.0)).cumprod()
    np.testing.assert_allclose(result.equity, expected, rtol=1e-9)
    assert result.stats.orders > 2
    assert 0 < result.stats.time_in_market < 1


def test_targets_do_not_look_ahead() -> None:
    close = _closes()
    cut = 250
    shocked = close.copy()
    shocked.iloc[cut + 1 :] *= 0.2
    base = bt.target_weights(close, SmaTrendModel(), SPEC, FULL, window=60)
    after = bt.target_weights(shocked, SmaTrendModel(), SPEC, FULL, window=60)
    pd.testing.assert_series_equal(base.iloc[: cut + 1], after.iloc[: cut + 1])


def test_costs_fees_and_delay_reduce_the_result() -> None:
    close = _closes()
    target = bt.target_weights(close, SmaTrendModel(), SPEC, FULL, window=60)
    free = bt.simulate(close, target, FULL)
    costly = bt.simulate(close, target, FULL, bt.CostModel(bps_per_side=20, fee_per_order=2.0))
    assert costly.stats.final_equity < free.stats.final_equity
    expected_fees = costly.stats.orders * 2.0
    assert costly.stats.costs_paid > expected_fees
    assert (costly.equity > 0).all()
    late = bt.simulate(close, target, FULL, delay=2)
    assert not np.allclose(late.equity, free.equity)


def test_rebalance_band_and_slice_weight() -> None:
    close = _closes()
    small = VolTargetSizing(target_vol_annual=2.0, symbol_weight=0.25, rebalance_band=0.10)
    target = bt.target_weights(close, SmaTrendModel(), SPEC, small, window=60)
    result = bt.simulate(close, target, small)
    assert result.weight.max() < 0.40  # a quarter slice, allowed to drift, never the account
    assert target.max() == pytest.approx(0.25)
    no_band = VolTargetSizing(target_vol_annual=2.0, symbol_weight=0.25, rebalance_band=0.0)
    assert bt.simulate(close, target, no_band).stats.orders > result.stats.orders


def test_start_keeps_the_account_flat_and_measures_from_that_date() -> None:
    close = _closes()
    target = bt.target_weights(close, SmaTrendModel(), SPEC, FULL, window=60)
    start = "2020-08-01"
    result = bt.simulate(close, target, FULL, initial_equity=1_000.0, start=start)
    assert str(result.equity.index[0].date()) == start
    assert result.equity.iloc[0] == pytest.approx(1_000.0, rel=0.02)  # at most one bar held
    held = (target.fillna(0.0) > 0).astype(float).shift(2).fillna(0.0)
    first = close.index.get_loc(pd.Timestamp(start))
    held.iloc[: first + 1] = 0.0  # the first fill is on the start bar, so it earns from the next
    expected = 1_000.0 * (1 + held * close.pct_change().fillna(0.0)).cumprod()
    np.testing.assert_allclose(result.equity, expected.loc[start:], rtol=1e-9)
    window = close.loc[start:]
    years = (window.index[-1] - window.index[0]).days / 365.25
    hold = (window.iloc[-1] / window.iloc[0]) ** (1 / years) - 1
    assert result.stats.buy_hold_return == pytest.approx(hold)
    with pytest.raises(ValueError, match="after the last bar"):
        bt.simulate(close, target, FULL, start="2030-01-01")


def test_trend_model_with_volatility_scaling_matches_the_reference() -> None:
    close = _closes(500, seed=5)
    spec = TrendSpec(lookbacks=(10, 20, 40), donchian_entry=8, donchian_exit=4, vol_window=5)
    sizing = VolTargetSizing(target_vol_annual=0.30, symbol_weight=0.5, rebalance_band=0.10)
    window = 120
    target = bt.target_weights(close, TrendFollowingModel(), spec, sizing, window=window)
    frame = live_feature_frame(close, window=window, trend=spec)
    vote = 0.5 * (frame["trend_return"] > 0) + 0.5 * (frame["donchian_long"] >= 0.5)
    scale = np.minimum(1.0, sizing.target_vol_annual / frame["realised_vol"])
    expected = (0.5 * vote * scale).where(vote > 0, 0.0)
    np.testing.assert_allclose(target.iloc[window:], expected.iloc[window:], rtol=1e-12)
    assert (scale.iloc[window:] < 1).any()  # the volatility scale really bites in this sample
    assert set(vote.iloc[window:].unique()) >= {0.0, 0.5, 1.0}
    result = bt.simulate(close, target, sizing, bt.CostModel(10.0, 1.0))
    assert result.weight.max() <= 0.5 * 1.5  # half the account, plus drift inside the band
    assert result.stats.orders > 5


@pytest.mark.parametrize("shares", [0, 100, 250, 300, 330, 400, 700])
@pytest.mark.parametrize("exposure", [0.0, 0.5, 1.0])
@pytest.mark.parametrize("vol", [0.30, 1.20])
async def test_rebalance_action_agrees_with_pre_trade_risk(
    shares: int, exposure: float, vol: float
) -> None:
    """The simulator's trade/no-trade rule is the live ``PreTradeRisk`` rule, case by case."""
    sizing = VolTargetSizing(target_vol_annual=0.60, symbol_weight=0.10, rebalance_band=0.10)
    price, portfolio = 300.0, 1_000_000.0
    broker = _Broker()
    if shares:
        broker.positions = [make_position(SYMBOL, shares, market_value=shares * price)]
    side = "SELL" if exposure == 0 else "BUY"
    live = await _risk(broker, vol_target=sizing).check(
        PreTradeRequest(
            symbol=SYMBOL,
            side=side,  # type: ignore[arg-type]
            confidence=1.0,
            price=price,
            target_exposure=exposure,
            realised_vol=vol,
        )
    )
    want = sizing.symbol_weight * exposure * sizing.vol_scale(vol)
    action = bt.rebalance_action(want, shares * price / portfolio, sizing)
    gap = want - shares * price / portfolio
    lot_value = 100 * price  # the fake broker's board lot; the backtester has no lots
    if action == bt.NO_TRADE:
        assert isinstance(live, Rejection)
        assert live.reason in {"within_rebalance_band", "at_target"}
    elif action == bt.REBALANCE and abs(gap) * portfolio < lot_value:
        assert live == Rejection("size_zero")  # the gap is under one lot
    else:
        assert isinstance(live, OrderIntent)
        assert live.side == ("SELL" if action == bt.EXIT or gap < 0 else "BUY")


def test_simulate_rejects_bad_input() -> None:
    close = _closes(50)
    target = pd.Series(0.0, index=close.index)
    with pytest.raises(ValueError, match="delay"):
        bt.simulate(close, target, FULL, delay=0)
    with pytest.raises(ValueError, match="share an index"):
        bt.simulate(close, target.iloc[:-1], FULL)
    with pytest.raises(ValueError, match="positive"):
        bt.simulate(close * 0, target, FULL)
    with pytest.raises(ValueError, match="initial_equity"):
        bt.simulate(close, target, FULL, initial_equity=0)
    flat = bt.simulate(close, target, FULL)
    assert flat.stats.orders == 0
    assert flat.stats.yearly_return == 0


def test_run_backtest_uses_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(monkeypatch)
    close = _closes(700)
    result = bt.run_backtest(close, config, bt.CostModel(20.0), symbol_weight=1.0)
    assert result.stats.bars == 700
    sliced = bt.run_backtest(close, config)
    assert sliced.weight.max() < 0.10  # the configured 4% slice
    with pytest.raises(ValueError, match="no leverage"):
        bt.run_backtest(close, config, symbol_weight=1.5)
    payload = config.model_dump()
    payload["model"]["type"] = "mean_reversion"
    with pytest.raises(ValueError, match="target exposure"):
        bt.run_backtest(close, AppConfig(**payload))


def test_cli_and_bar_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FUTU_ACCOUNT_ID", "0")
    monkeypatch.setenv("FUTU_SECURITY_FIRM", "FUTUSECURITIES")
    close = _closes(700)
    bars = tmp_path / "bars.csv"
    close.rename("close").rename_axis("timestamp").to_csv(bars)
    args = ["--config", "config/config.crypto.sma.yaml", "--bars", str(bars)]
    assert bt.main([*args, "--symbol-weight", "1.0", "--cost-bps", "20"]) == 0
    whole = json.loads(capsys.readouterr().out)
    assert whole["bars"] == 700
    assert bt.main([*args, "--symbol-weight", "1.0", "--start", "2021-06-01"]) == 0
    late = json.loads(capsys.readouterr().out)
    assert late["start"] == "2021-06-01"
    assert late["bars"] < whole["bars"]
    tz_bars = tmp_path / "tz.csv"
    close.rename("close").tz_localize("Asia/Hong_Kong").rename_axis("time_key").to_csv(tz_bars)
    assert bt.load_closes(str(tz_bars)).index.tz is None

    bad = tmp_path / "bad.csv"
    bad.write_text("timestamp,open\n2020-01-01,1\n")
    with pytest.raises(ValueError, match="close"):
        bt.load_closes(str(bad))
    empty = tmp_path / "empty.csv"
    empty.write_text("date,close\n")
    with pytest.raises(ValueError, match="no usable"):
        bt.load_closes(str(empty))
