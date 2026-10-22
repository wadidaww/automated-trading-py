"""Walk-forward backtest and CLI for Monte Carlo voting plans.

``python -m trader.research.plan_backtest backtest --spec config/montecarlo.example.yaml``
trains the voting ensemble on each walk-forward training window, makes a plan for every test
bar, and executes the plans on the real bars that follow (limit entry against the bar low, stop
checked before take-profit, time exit at the horizon close, costs on both sides).

``python -m trader.research.plan_backtest plan --spec ...`` trains on all history and prints
today's plan per symbol. Plans are research output: nothing here places orders.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, Field

from trader.data.features import FeatureSpec
from trader.research.dataset import Dataset, build_dataset, feature_panel
from trader.research.grid_search import (
    DataSection,
    ValidationSection,
    _feature_spec,
    code_sha,
    data_hash,
    load_bars,
)
from trader.research.labels import LabelSpec
from trader.research.monte_carlo import (
    EnsembleSettings,
    MemberPool,
    SimulationSettings,
    VotingEnsemble,
    make_plans,
    standardized_shocks,
)
from trader.research.scoring import strategy_stats
from trader.research.validation import purged_walk_forward


class PoolSection(BaseModel):
    """An estimator members may draw, with hyperparameter options (lists)."""

    estimator: str
    params: dict[str, Any] = Field(default_factory=dict)


def _default_pool() -> list[PoolSection]:
    return [
        PoolSection(
            estimator="lightgbm", params={"n_estimators": [100, 300], "num_leaves": [7, 15]}
        ),
        PoolSection(estimator="linear", params={"alpha": [1.0, 10.0, 100.0]}),
        PoolSection(estimator="extra_trees", params={"min_samples_leaf": [50, 100]}),
    ]


class EnsembleSection(BaseModel):
    """Ensemble size, randomisation and vote rule."""

    n_members: int = 30
    feature_fraction: float = 0.6
    sample_fraction: float = 0.8
    block: int = 20
    vote_margin: float = 0.0
    vote_threshold: float = 0.6
    seed: int = 0
    pool: list[PoolSection] = Field(default_factory=_default_pool)


class SimulationSection(BaseModel):
    """Monte Carlo paths and bracket rules."""

    n_paths: int = 1000
    entry_window: int = 2
    entry_fill_prob: float = 0.5
    take_profit_prob: float = 0.35
    stop_prob: float = 0.1
    min_expected_value: float = 0.0
    seed: int = 0


class ExecutionSection(BaseModel):
    """How plans are executed in the backtest."""

    cost_bps: float = 20.0
    long_only: bool = True
    periods_per_year: int = 252


class PlanSpec(BaseModel):
    """Everything the Monte Carlo backtest and planner need, as loaded from YAML."""

    data: DataSection = Field(default_factory=DataSection)
    features: dict[str, Any] = Field(default_factory=dict)
    label: dict[str, Any] = Field(default_factory=lambda: {"horizon": 5})
    ensemble: EnsembleSection = Field(default_factory=EnsembleSection)
    simulation: SimulationSection = Field(default_factory=SimulationSection)
    validation: ValidationSection = Field(default_factory=ValidationSection)
    execution: ExecutionSection = Field(default_factory=ExecutionSection)
    output_dir: str = "data/research"

    def feature_spec(self) -> FeatureSpec:
        """Parsed feature spec."""
        return _feature_spec(self.features)

    def label_spec(self) -> LabelSpec:
        """Parsed label spec; plans need a forward-return (regression) label.

        Raises:
            ValueError: On a triple-barrier label.
        """
        label = LabelSpec(**self.label)
        if label.kind != "forward_return":
            raise ValueError("Monte Carlo plans need a forward_return label")
        return label

    def ensemble_settings(self) -> tuple[list[MemberPool], EnsembleSettings]:
        """Pool and settings for ``VotingEnsemble``."""
        values = self.ensemble.model_dump(exclude={"pool"})
        pool = [MemberPool(p.estimator, p.params) for p in self.ensemble.pool]
        return pool, EnsembleSettings(**values)

    def simulation_settings(self) -> SimulationSettings:
        """Simulation settings with the execution cost folded in."""
        return SimulationSettings(**self.simulation.model_dump(), cost_bps=self.execution.cost_bps)


@dataclass(frozen=True, slots=True)
class Trade:
    """One executed plan."""

    symbol: str
    decided: pd.Timestamp
    side: Literal["BUY", "SELL"]
    entry_time: pd.Timestamp
    entry: float
    exit_time: pd.Timestamp
    exit: float
    reason: Literal["take_profit", "stop", "time"]
    net_return: float


def _execute_symbol(
    symbol: str,
    bars: pd.DataFrame,
    plans: pd.DataFrame,
    cost_bps: float,
    long_only: bool,
) -> tuple[list[Trade], pd.Series, int]:
    """Run one symbol's plans in time order, one position at a time.

    Returns:
        ``(trades, daily_returns, plans_considered)``; daily returns are marked to the close.
    """
    cost = cost_bps / 1e4
    index = bars.index
    opens = bars["open"].to_numpy(float) if "open" in bars else bars["close"].to_numpy(float)
    highs = bars["high"].to_numpy(float) if "high" in bars else bars["close"].to_numpy(float)
    lows = bars["low"].to_numpy(float) if "low" in bars else bars["close"].to_numpy(float)
    closes = bars["close"].to_numpy(float)
    daily = np.zeros(len(index))
    trades: list[Trade] = []
    considered = 0
    busy_until = -1
    for decided, plan in plans.sort_index().iterrows():
        t = int(index.get_loc(decided))
        if t <= busy_until or plan["side"] == "HOLD":
            continue
        if plan["side"] == "SELL" and long_only:
            continue
        considered += 1
        sign = 1.0 if plan["side"] == "BUY" else -1.0
        entry, tp, sl = float(plan["entry"]), float(plan["take_profit"]), float(plan["stop_loss"])
        horizon = int(plan["horizon"])
        last = min(t + horizon, len(index) - 1)
        window_end = min(t + int(plan.get("entry_window", 2)), last)
        fill_bar, fill_price = -1, math.nan
        for j in range(t + 1, window_end + 1):
            if sign * (opens[j] - entry) <= 0:
                fill_bar, fill_price = j, opens[j]
                break
            if (sign > 0 and lows[j] <= entry) or (sign < 0 and highs[j] >= entry):
                fill_bar, fill_price = j, entry
                break
        if fill_bar < 0:
            continue
        exit_bar, exit_price, reason = last, closes[last], "time"
        for j in range(fill_bar, last + 1):
            adverse = lows[j] <= sl if sign > 0 else highs[j] >= sl
            favourable = highs[j] >= tp if sign > 0 else lows[j] <= tp
            gap_stop = j > fill_bar and sign * (opens[j] - sl) <= 0
            if gap_stop:
                exit_bar, exit_price, reason = j, opens[j], "stop"
                break
            if adverse:
                exit_bar, exit_price, reason = j, sl, "stop"
                break
            if j > fill_bar and favourable:
                gap_tp = sign * (opens[j] - tp) >= 0
                exit_bar, exit_price, reason = j, opens[j] if gap_tp else tp, "take_profit"
                break
        # Mark to market: entry bar from the fill price, exit bar to the exit price.
        prev = fill_price
        for j in range(fill_bar, exit_bar + 1):
            mark = exit_price if j == exit_bar else closes[j]
            daily[j] += sign * (mark / prev - 1)
            prev = mark
        daily[fill_bar] -= cost
        daily[exit_bar] -= cost
        net = sign * (exit_price / fill_price - 1) - 2 * cost
        trades.append(
            Trade(
                symbol=symbol,
                decided=decided,
                side=plan["side"],
                entry_time=index[fill_bar],
                entry=float(fill_price),
                exit_time=index[exit_bar],
                exit=float(exit_price),
                reason=reason,  # type: ignore[arg-type]
                net_return=net,
            )
        )
        busy_until = exit_bar
    return trades, pd.Series(daily, index=index), considered


def execute_plans(
    bars_by_symbol: dict[str, pd.DataFrame], plans: pd.DataFrame, execution: ExecutionSection
) -> tuple[pd.DataFrame, pd.Series, int]:
    """Execute every symbol's plans; returns trades, equal-weight daily returns, plans tried."""
    all_trades: list[Trade] = []
    daily: dict[str, pd.Series] = {}
    considered = 0
    for symbol, bars in bars_by_symbol.items():
        mask = plans.index.get_level_values("symbol") == symbol
        if not mask.any():
            continue
        symbol_plans = plans[mask].droplevel("symbol")
        trades, returns, tried = _execute_symbol(
            symbol, bars, symbol_plans, execution.cost_bps, execution.long_only
        )
        all_trades.extend(trades)
        first = symbol_plans.index.min()
        daily[symbol] = returns[returns.index >= first]
        considered += tried
    frame = pd.DataFrame([asdict(t) for t in all_trades]) if all_trades else pd.DataFrame()
    portfolio = pd.DataFrame(daily).fillna(0.0).mean(axis=1) if daily else pd.Series(dtype=float)
    return frame, portfolio, considered


def walk_forward_plans(
    spec: PlanSpec, bars_by_symbol: dict[str, pd.DataFrame], dataset: Dataset
) -> pd.DataFrame:
    """Out-of-sample plans: fit on each purged training window, plan the following test block."""
    label = spec.label_spec()
    pool, settings = spec.ensemble_settings()
    sim = spec.simulation_settings()
    validation = spec.validation
    splits = purged_walk_forward(
        dataset.dates,
        n_splits=validation.n_splits,
        horizon=label.horizon,
        embargo=validation.embargo,
        min_train=validation.min_train,
        expanding=validation.expanding,
    )
    plans: list[pd.DataFrame] = []
    for split in splits:
        train = dataset.rows_for(split.train_dates)
        test = dataset.rows_for(split.test_dates)
        if not train.any() or not test.any():
            continue
        ensemble = VotingEnsemble(pool, settings).fit(dataset.X[train], dataset.y[train])
        cutoff = split.train_dates[-1]
        shocks = standardized_shocks(
            {s: b[b.index <= cutoff] for s, b in bars_by_symbol.items()}, label.vol_window
        )
        fold_plans = make_plans(
            ensemble,
            dataset.X[test],
            dataset.close,
            dataset.volatility,
            shocks,
            label.horizon,
            label.vol_scale,
            sim,
        )
        fold_plans["fold"] = split.fold
        fold_plans["entry_window"] = sim.entry_window
        plans.append(fold_plans)
    if not plans:
        raise ValueError("walk-forward produced no plans")
    return pd.concat(plans).sort_index()


def vote_calibration(plans: pd.DataFrame, dataset: Dataset, horizon: int) -> pd.DataFrame:
    """Directional hit rate of the voted side by vote-share bucket.

    If bigger majorities are not more often right, the vote adds little beyond noise.
    """
    realised = dataset.y.loc[plans.index]
    voted = plans[["share_up", "share_down"]].max(axis=1)
    direction = np.where(plans["share_up"] >= plans["share_down"], 1.0, -1.0)
    frame = pd.DataFrame(
        {"share": voted, "hit": (np.sign(realised) == direction).astype(float)}
    ).dropna()
    buckets = pd.cut(frame["share"], bins=[0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], include_lowest=True)
    table = frame.groupby(buckets, observed=True)["hit"].agg(["count", "mean"])
    table.index = table.index.astype(str)
    return table.rename(columns={"mean": "hit_rate"})


def run_backtest(spec: PlanSpec, run_dir: Path) -> dict[str, Any]:
    """Walk-forward plans, execution and summary files; returns the summary."""
    bars = load_bars(spec.data)
    label = spec.label_spec()
    dataset = build_dataset(bars, spec.feature_spec(), label)
    plans = walk_forward_plans(spec, bars, dataset)
    trades, daily, considered = execute_plans(bars, plans, spec.execution)
    stats = strategy_stats(daily, pd.Series(dtype=float), spec.execution.periods_per_year)
    calibration = vote_calibration(plans, dataset, label.horizon)
    run_dir.mkdir(parents=True, exist_ok=True)
    plans.to_csv(run_dir / "plans.csv")
    trades.to_csv(run_dir / "trades.csv", index=False)
    calibration.to_csv(run_dir / "vote_calibration.csv")
    n_trades = len(trades)
    reasons = trades["reason"].value_counts().to_dict() if n_trades else {}
    sides = plans["side"].value_counts().to_dict()
    summary: dict[str, Any] = {
        "run_dir": str(run_dir),
        "data_hash": data_hash(bars),
        "code_sha": code_sha(),
        "n_plans": len(plans),
        "plan_sides": sides,
        "plans_attempted": considered,
        "n_trades": n_trades,
        "fill_rate": n_trades / considered if considered else None,
        "exit_reasons": reasons,
        "mean_trade_return": float(trades["net_return"].mean()) if n_trades else None,
        "trade_win_rate": float((trades["net_return"] > 0).mean()) if n_trades else None,
        "sharpe_annual": stats.sharpe_annual if math.isfinite(stats.sharpe_annual) else None,
        "annual_return": stats.annual_return if math.isfinite(stats.annual_return) else None,
        "max_drawdown": stats.max_drawdown,
        "n_days": stats.n_obs,
        "vote_calibration": calibration.reset_index().to_dict(orient="records"),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), "utf-8")
    return summary


def latest_plans(spec: PlanSpec) -> pd.DataFrame:
    """Train on all labelled history and plan the most recent bar of every symbol."""
    bars = load_bars(spec.data)
    label = spec.label_spec()
    features = spec.feature_spec()
    dataset = build_dataset(bars, features, label)
    pool, settings = spec.ensemble_settings()
    ensemble = VotingEnsemble(pool, settings).fit(dataset.X, dataset.y)
    # The latest bars have no label yet, so take them from the unfiltered feature panel.
    panel = feature_panel(bars, features, label.vol_window)
    columns = list(dataset.X.columns)
    complete = panel.dropna(subset=[*columns, "__volatility"])
    latest = complete.groupby(level="symbol").tail(1)
    if latest.empty:
        raise ValueError("no symbol has complete features on its latest bars")
    shocks = standardized_shocks(bars, label.vol_window)
    return make_plans(
        ensemble,
        latest[columns],
        latest["__close"],
        latest["__volatility"],
        shocks,
        label.horizon,
        label.vol_scale,
        spec.simulation_settings(),
    )


def load_plan_spec(path: str) -> PlanSpec:
    """Parse and validate a Monte Carlo spec YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return PlanSpec.model_validate(raw)


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Monte Carlo voting plans")
    parser.add_argument("command", choices=["backtest", "plan"])
    parser.add_argument("--spec", default="config/montecarlo.example.yaml")
    parser.add_argument("--synthetic", action="store_true", help="use synthetic bars")
    args = parser.parse_args(argv)
    spec = load_plan_spec(args.spec)
    if args.synthetic:
        spec.data.source = "synthetic"
    if args.command == "plan":
        plans = latest_plans(spec)
        print(plans.to_string())
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary = run_backtest(spec, Path(spec.output_dir) / f"mc-{stamp}")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
