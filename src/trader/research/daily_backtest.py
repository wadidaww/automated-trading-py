"""Daily-bar backtest of a target-exposure model, on the code the daily signal job runs.

Each close is replayed through ``live_feature_frame`` (what ``DataStage`` computes bar by bar),
the configured model's ``predict`` and ``VolTargetSizing`` (the sizing ``PreTradeRisk`` applies),
so the decision inputs are the job's own code. The decision made on the close of bar ``t`` is
filled at the close of bar ``t + delay`` (default 1) and earns from the bar after. Like the job,
it decides nothing until ``pipeline.data_window_size`` closes exist.

The fill is more generous than the job's. The job rests a LIMIT at the previous close, rejects a
price more than ``risk.price_band_pct`` from it and sends one order a session, so on a gap day an
exit can be late or missed; here the whole gap always fills at the next close. Drawdowns are
therefore understated. Also left out: order caps, board lots and quantity steps (``PreTradeRisk``
applies them per order from live broker data) and partial fills. One symbol per run, long only;
cash earns nothing.

Run::

    python -m trader.research.daily_backtest --config config/config.crypto.sma.yaml \
        --bars data/raw/CC.BTCUSD_K_DAY.csv --symbol-weight 1.0 --cost-bps 20

Research only: nothing here sends an order.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass

import pandas as pd

from trader.config import TARGET_MODEL_TYPES, AppConfig, load_config
from trader.data.features import TrendSpec, live_feature_frame
from trader.model.base import ISignalModel, Signal
from trader.model.registry import build_model
from trader.risk.pre_trade import VolTargetSizing
from trader.utils.logger import get_logger

logger = get_logger("daily_backtest")

EXIT = "exit"
REBALANCE = "rebalance"
NO_TRADE = "no_trade"
DAYS_PER_YEAR = 365.25
BPS = 1e4


@dataclass(frozen=True, slots=True)
class CostModel:
    """Trading costs: basis points of traded notional per side plus a fixed fee per order."""

    bps_per_side: float = 0.0
    fee_per_order: float = 0.0


@dataclass(frozen=True, slots=True)
class BacktestStats:
    """Summary of one run. Returns are yearly and compounded; ``max_drawdown`` is negative."""

    start: str
    end: str
    bars: int
    yearly_return: float
    yearly_vol: float
    sharpe: float
    max_drawdown: float
    buy_hold_return: float
    buy_hold_drawdown: float
    orders: int
    orders_per_year: float
    time_in_market: float
    costs_paid: float
    final_equity: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Per-bar equity, held weight and target weight, plus the summary."""

    equity: pd.Series
    weight: pd.Series
    target: pd.Series
    stats: BacktestStats


def target_weights(
    close: pd.Series,
    model: ISignalModel,
    spec: TrendSpec,
    sizing: VolTargetSizing,
    window: int,
) -> pd.Series:
    """Target share of the portfolio decided on each close; NaN where the model says HOLD.

    Row ``t`` only uses closes up to ``t``. Rows before ``window`` closes exist are NaN: the
    daily job refuses to decide on a shorter history. A BUY needs a usable volatility, as in
    ``PreTradeRisk``; without one the row is NaN (no trade).
    """
    features = live_feature_frame(close, window=window, trend=spec)
    out: list[float] = []
    for i in range(len(features)):
        if i < window - 1:
            out.append(math.nan)
            continue
        row = features.iloc[[i]]
        prediction = model.predict(row)
        exposure = prediction.target_exposure
        if prediction.signal is Signal.HOLD or exposure is None:
            out.append(math.nan)
        elif exposure == 0:
            out.append(0.0)
        else:
            vol = float(row["realised_vol"].iloc[0])
            if not math.isfinite(vol) or vol <= 0:
                out.append(math.nan)
            else:
                out.append(sizing.symbol_weight * exposure * sizing.vol_scale(vol))
    return pd.Series(out, index=close.index, name="target")


def rebalance_action(want: float, held: float, sizing: VolTargetSizing) -> str:
    """What ``PreTradeRisk`` does with a target: ``EXIT``, ``REBALANCE`` or ``NO_TRADE``.

    ``want`` and ``held`` are shares of the portfolio. A zero target sells everything held; any
    other target trades only when the gap is at least ``rebalance_band`` of the symbol's budget.
    """
    if want == 0:
        return EXIT if held > 0 else NO_TRADE
    gap = want - held
    if gap == 0 or abs(gap) < sizing.rebalance_band * sizing.symbol_weight:
        return NO_TRADE
    return REBALANCE


def simulate(
    close: pd.Series,
    target: pd.Series,
    sizing: VolTargetSizing,
    costs: CostModel | None = None,
    initial_equity: float = 10_000.0,
    delay: int = 1,
    start: str | None = None,
) -> BacktestResult:
    """Trade ``close`` towards ``target`` with the live rebalance band, after costs.

    With ``start`` the account stays flat until that date (earlier bars are warm-up only) and
    the result, including buy-and-hold, is measured from the first bar on or after it.

    The target from bar ``t`` is compared with the position held at bar ``t + delay`` and traded
    at that bar's close: a zero target sells everything, any other target trades only when the gap
    is at least ``rebalance_band`` of the symbol's budget (the ``PreTradeRisk`` rule).

    Raises:
        ValueError: When the inputs are empty, misaligned, non-positive or ``delay`` < 1.
    """
    costs = costs or CostModel()
    if delay < 1:
        raise ValueError("delay must be at least 1 bar: a close cannot be traded before it prints")
    if close.empty or not close.index.equals(target.index):
        raise ValueError("close and target must be non-empty and share an index")
    if not (close > 0).all():
        raise ValueError("closes must be positive")
    if initial_equity <= 0:
        raise ValueError("initial_equity must be positive")
    prices = close.astype(float).to_numpy()
    wanted = target.shift(delay)
    if start is not None:
        wanted = wanted.where(close.index >= pd.Timestamp(start))
        if not wanted.index[-1] >= pd.Timestamp(start):
            raise ValueError("start is after the last bar")
    wanted = wanted.to_numpy()
    cash, units, orders, paid = initial_equity, 0.0, 0, 0.0
    equity: list[float] = []
    weights: list[float] = []
    for price, want in zip(prices, wanted, strict=True):
        value = units * price
        total = cash + value
        if math.isfinite(want) and total > 0:
            held = value / total
            gap = want - held
            action = rebalance_action(want, held, sizing)
            exit_all = action == EXIT
            if action != NO_TRADE:
                trade = -value if exit_all else gap * total
                if trade > 0:  # a buy and its costs are paid from cash: never borrow
                    rate = costs.bps_per_side / BPS
                    trade = min(trade, max(0.0, (cash - costs.fee_per_order) / (1 + rate)))
                cost = abs(trade) * costs.bps_per_side / BPS + costs.fee_per_order
                if trade != 0 and cash - trade - cost >= -1e-9:  # a sale must cover its own fee
                    units = 0.0 if exit_all else units + trade / price
                    cash -= trade + cost
                    orders += 1
                    paid += cost
        total = cash + units * price
        equity.append(total)
        weights.append(units * price / total if total > 0 else 0.0)
    eq = pd.Series(equity, index=close.index, name="equity")
    weight = pd.Series(weights, index=close.index, name="weight")
    if start is not None:
        eq, weight, close, target = (x.loc[start:] for x in (eq, weight, close, target))
    stats = _stats(eq, weight, close, orders, paid, initial_equity)
    return BacktestResult(equity=eq, weight=weight, target=target, stats=stats)


def run_backtest(
    close: pd.Series,
    config: AppConfig,
    costs: CostModel | None = None,
    symbol_weight: float | None = None,
    initial_equity: float = 10_000.0,
    delay: int = 1,
    start: str | None = None,
) -> BacktestResult:
    """Backtest the model, features and sizing that ``config`` gives the daily signal job.

    Args:
        close: Daily closes, oldest first, indexed by date.
        config: A config whose ``model.type`` states a target exposure (``trend``, ``sma_trend``).
        costs: Cost model; none means free trading.
        symbol_weight: Overrides ``risk.vol_target.symbol_weight`` (use 1.0 to judge the rule on
            the whole account instead of its configured slice).
        initial_equity: Starting cash.
        delay: Bars between the decision and the fill.
        start: First date to trade and measure from; earlier bars only warm the features up.

    Raises:
        ValueError: When the config's model does not state a target exposure.
    """
    if config.model.type not in TARGET_MODEL_TYPES:
        raise ValueError(f"model.type '{config.model.type}' does not state a target exposure")
    trend = config.model.trend
    spec = TrendSpec(
        lookbacks=tuple(trend.lookbacks),
        donchian_entry=trend.donchian_entry,
        donchian_exit=trend.donchian_exit,
        vol_window=trend.vol_window,
        bars_per_year=trend.bars_per_year,
        sma_window=trend.sma_window,
    )
    vol_target = config.risk.vol_target
    sizing = VolTargetSizing(
        target_vol_annual=vol_target.target_vol_annual,
        symbol_weight=vol_target.symbol_weight if symbol_weight is None else symbol_weight,
        rebalance_band=vol_target.rebalance_band,
    )
    if not 0 < sizing.symbol_weight <= 1:
        raise ValueError("symbol_weight must be in (0, 1]: there is no leverage")
    model = build_model(config.model.type, config.model)
    target = target_weights(close, model, spec, sizing, config.pipeline.data_window_size)
    return simulate(close, target, sizing, costs, initial_equity, delay, start)


def load_closes(path: str) -> pd.Series:
    """Closes from a bar CSV: a ``close`` column and a date column or index (fetcher cache layout).

    Raises:
        ValueError: When there is no ``close`` column or no parseable dates.
    """
    frame = pd.read_csv(path)
    if "close" not in frame.columns:
        raise ValueError(f"{path} has no 'close' column")
    date_column = next((c for c in ("timestamp", "time_key", "date", "time") if c in frame), None)
    if date_column is None:
        date_column = frame.columns[0]
    index = pd.DatetimeIndex(pd.to_datetime(frame[date_column]))
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    close = pd.Series(frame["close"].astype(float).to_numpy(), index=index, name="close")
    close = close[~close.index.duplicated(keep="last")].sort_index().dropna()
    if close.empty:
        raise ValueError(f"{path} has no usable closes")
    return close


def _stats(
    equity: pd.Series,
    weight: pd.Series,
    close: pd.Series,
    orders: int,
    paid: float,
    initial_equity: float,
) -> BacktestStats:
    years = max((equity.index[-1] - equity.index[0]).days / DAYS_PER_YEAR, 1 / DAYS_PER_YEAR)
    returns = equity.pct_change().dropna()
    bars_per_year = len(returns) / years if len(returns) else 0.0
    spread = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    growth = float(equity.iloc[-1]) / initial_equity
    hold = close / float(close.iloc[0])
    return BacktestStats(
        start=str(equity.index[0].date()),
        end=str(equity.index[-1].date()),
        bars=len(equity),
        yearly_return=growth ** (1 / years) - 1 if growth > 0 else -1.0,
        yearly_vol=spread * math.sqrt(bars_per_year),
        sharpe=float(returns.mean()) / spread * math.sqrt(bars_per_year) if spread > 0 else 0.0,
        max_drawdown=_drawdown(equity / initial_equity),
        buy_hold_return=float(hold.iloc[-1]) ** (1 / years) - 1,
        buy_hold_drawdown=_drawdown(hold),
        orders=orders,
        orders_per_year=orders / years,
        time_in_market=float((weight > 0).mean()),
        costs_paid=paid,
        final_equity=float(equity.iloc[-1]),
    )


def _drawdown(curve: pd.Series) -> float:
    return float((curve / curve.cummax() - 1).min())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; prints the summary as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument(
        "--config", required=True, help="AppConfig YAML with a target-exposure model"
    )
    parser.add_argument("--bars", required=True, help="daily bar CSV with a close column")
    parser.add_argument("--symbol-weight", type=float, default=None)
    parser.add_argument("--cost-bps", type=float, default=0.0, help="per side, of traded notional")
    parser.add_argument("--fee", type=float, default=0.0, help="fixed fee per order")
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--delay", type=int, default=1, help="bars between decision and fill")
    parser.add_argument(
        "--start", default=None, help="first date to trade (warm-up bars before it)"
    )
    parser.add_argument("--end", default=None)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    close = load_closes(args.bars).loc[: args.end]
    costs = CostModel(bps_per_side=args.cost_bps, fee_per_order=args.fee)
    result = run_backtest(
        close, config, costs, args.symbol_weight, args.equity, args.delay, args.start
    )
    logger.info("daily_backtest_complete", **asdict(result.stats))
    print(json.dumps(asdict(result.stats), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
