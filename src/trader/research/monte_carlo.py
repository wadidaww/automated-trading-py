"""Monte Carlo trading plans decided by a majority vote of many randomised models.

The idea in three steps:

1. **Vote.** ``VotingEnsemble`` trains many members. Each member draws a random estimator and
   hyperparameters from a pool, a random subset of features, and a block bootstrap of the
   training dates. Each member predicts the (volatility-scaled) forward return and votes up,
   down or abstain. The side with at least ``vote_threshold`` of the votes wins; otherwise HOLD.
2. **Simulate.** ``simulate_paths`` draws price paths over the label horizon. Drift is the median
   prediction of the winning voters; shocks are standardised historical returns resampled from
   the training data (fat tails kept) and scaled by the current trailing volatility.
3. **Plan.** ``plan_from_paths`` turns the paths into a bracket: a limit entry the paths reach
   with probability ``entry_fill_prob``, a take-profit reached with ``take_profit_prob``, and a
   stop at the ``stop_prob`` quantile of the path lows. The bracket is replayed on the same paths
   to estimate fill, target and stop probabilities and the expected net return; a plan with
   non-positive expected value becomes HOLD.

Caveats worth keeping in mind: members trained on the same data are correlated, so 50 votes are
not 50 independent opinions; the paths only reflect the drift and shocks fed in; and simulated
paths use closes only, so intrabar touches are undercounted. Judge the plans by
``plan_backtest`` on real highs and lows, net of costs, never by the simulated probabilities.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Final, Literal

import numpy as np
import pandas as pd

from trader.model.tabular import ESTIMATORS, TabularModel

Side = Literal["BUY", "SELL", "HOLD"]
_SHOCK_CLIP: Final = 8.0


@dataclass(frozen=True, slots=True)
class MemberPool:
    """One estimator the ensemble may draw, with a grid of hyperparameter options."""

    estimator: str
    params: dict[str, list[Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EnsembleSettings:
    """How members are randomised and how their votes are counted."""

    n_members: int = 30
    feature_fraction: float = 0.6
    sample_fraction: float = 0.8
    block: int = 20
    vote_margin: float = 0.0
    vote_threshold: float = 0.6
    seed: int = 0


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    """Monte Carlo path count and how the bracket levels are chosen."""

    n_paths: int = 1000
    entry_window: int = 2
    entry_fill_prob: float = 0.5
    take_profit_prob: float = 0.35
    stop_prob: float = 0.1
    cost_bps: float = 20.0
    min_expected_value: float = 0.0
    seed: int = 0


@dataclass(frozen=True, slots=True)
class Member:
    """A trained ensemble member and what it was trained on."""

    estimator: str
    params: dict[str, Any]
    features: list[str]
    model: TabularModel


@dataclass(frozen=True, slots=True)
class Vote:
    """Outcome of the majority vote for one row."""

    side: Side
    share_up: float
    share_down: float
    consensus: float
    dispersion: float


@dataclass(frozen=True, slots=True)
class TradePlan:
    """A bracket order plan: where to enter, where to take profit, where to stop.

    Prices are absolute. For BUY the entry is a limit at or below ``close``; for SELL (short) it
    is a limit at or above ``close`` and the take-profit sits below the entry.
    """

    side: Side
    close: float
    entry: float
    take_profit: float
    stop_loss: float
    horizon: int
    share_up: float
    share_down: float
    expected_return: float
    p_fill: float
    p_take_profit: float
    p_stop: float
    expected_value: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly dict with rounded prices."""
        out = asdict(self)
        for key in ("close", "entry", "take_profit", "stop_loss"):
            out[key] = round(out[key], 6) if math.isfinite(out[key]) else None
        return out


def _sample_params(grid: dict[str, list[Any]], rng: np.random.Generator) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, options in grid.items():
        choices = options if isinstance(options, list) else [options]
        params[key] = choices[int(rng.integers(len(choices)))]
    return params


def block_bootstrap_rows(
    timestamps: pd.Index, fraction: float, block: int, rng: np.random.Generator
) -> np.ndarray:
    """Row positions from consecutive date blocks drawn with replacement.

    Blocks keep serial dependence inside each sample, which a row-by-row bootstrap would break.
    """
    codes, uniques = pd.factorize(timestamps, sort=True)
    n_dates = len(uniques)
    block = max(1, min(block, n_dates))
    rows_by_date = pd.Series(np.arange(len(codes))).groupby(codes).apply(np.asarray)
    target = max(1, int(round(fraction * n_dates)))
    picked: list[np.ndarray] = []
    total = 0
    while total < target:
        start = int(rng.integers(0, n_dates - block + 1))
        for date_code in range(start, start + block):
            picked.append(rows_by_date.iloc[date_code])
        total += block
    return np.concatenate(picked)


class VotingEnsemble:
    """Many randomised regression members whose signs are counted as votes."""

    def __init__(self, pool: list[MemberPool], settings: EnsembleSettings | None = None) -> None:
        """Validate the pool.

        Raises:
            ValueError: On an empty pool or an unknown estimator.
        """
        if not pool:
            raise ValueError("the ensemble needs at least one estimator in its pool")
        unknown = [p.estimator for p in pool if p.estimator not in ESTIMATORS]
        if unknown:
            raise ValueError(f"unknown estimators {unknown}; known: {sorted(ESTIMATORS)}")
        self.pool = pool
        self.settings = settings or EnsembleSettings()
        self.members: list[Member] = []

    def fit(self, X: pd.DataFrame, y: pd.Series) -> VotingEnsemble:
        """Train ``n_members`` randomised members on ``X`` indexed by ``(timestamp, symbol)``."""
        rng = np.random.default_rng(self.settings.seed)
        columns = list(X.columns)
        n_features = max(1, int(round(self.settings.feature_fraction * len(columns))))
        timestamps = X.index.get_level_values("timestamp")
        self.members = []
        for _ in range(self.settings.n_members):
            choice = self.pool[int(rng.integers(len(self.pool)))]
            params = _sample_params(choice.params, rng)
            features = sorted(rng.choice(columns, size=n_features, replace=False).tolist())
            rows = block_bootstrap_rows(
                timestamps, self.settings.sample_fraction, self.settings.block, rng
            )
            model = TabularModel(estimator=choice.estimator, task="regression", params=params)
            model.fit(X.iloc[rows][features], y.iloc[rows])
            self.members.append(Member(choice.estimator, params, features, model))
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Member predictions, shape ``(rows, members)``.

        Raises:
            RuntimeError: When ``fit`` has not run.
        """
        if not self.members:
            raise RuntimeError("the ensemble is not fitted")
        return np.column_stack([m.model.score(X[m.features]) for m in self.members])

    def vote(self, predictions: np.ndarray) -> list[Vote]:
        """Count each row's votes; the consensus is the median prediction of the winning side."""
        margin = self.settings.vote_margin
        threshold = self.settings.vote_threshold
        votes: list[Vote] = []
        for row in np.atleast_2d(predictions):
            up, down = row > margin, row < -margin
            share_up, share_down = float(up.mean()), float(down.mean())
            if share_up >= threshold and share_up > share_down:
                side: Side = "BUY"
                consensus = float(np.median(row[up]))
            elif share_down >= threshold and share_down > share_up:
                side = "SELL"
                consensus = float(np.median(row[down]))
            else:
                side = "HOLD"
                consensus = float(np.median(row))
            votes.append(Vote(side, share_up, share_down, consensus, float(np.std(row))))
        return votes


def standardized_shocks(bars_by_symbol: dict[str, pd.DataFrame], vol_window: int) -> np.ndarray:
    """Daily log returns divided by the trailing volatility known the day before.

    Pooled across symbols; this is the shock distribution the simulator resamples.
    """
    shocks: list[np.ndarray] = []
    for bars in bars_by_symbol.values():
        log_ret = np.log(bars["close"].astype(float)).diff()
        vol = log_ret.rolling(vol_window).std().shift(1)
        z = (log_ret / vol).replace([np.inf, -np.inf], np.nan).dropna()
        shocks.append(z.clip(-_SHOCK_CLIP, _SHOCK_CLIP).to_numpy())
    pooled = np.concatenate(shocks) if shocks else np.array([])
    if pooled.size < 30:
        raise ValueError(f"need at least 30 standardised returns to simulate, got {pooled.size}")
    # Re-standardise so the scale comes from the current volatility, not the pool.
    return (pooled - pooled.mean()) / pooled.std()


def simulate_paths(
    close: float,
    volatility: float,
    expected_return: float,
    horizon: int,
    shocks: np.ndarray,
    n_paths: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Close prices for ``n_paths`` paths over ``horizon`` bars, shape ``(n_paths, horizon)``.

    Args:
        close: Price at the decision time.
        volatility: Per-bar log-return volatility.
        expected_return: Expected log return over the whole horizon (the drift).
        horizon: Bars to simulate.
        shocks: Standardised returns to resample.
        n_paths: Number of paths.
        rng: Random generator.
    """
    draws = shocks[rng.integers(0, len(shocks), size=(n_paths, horizon))]
    steps = expected_return / horizon + volatility * draws
    return close * np.exp(np.cumsum(steps, axis=1))


def _long_bracket(
    close: float, paths: np.ndarray, sim: SimulationSettings
) -> tuple[float, float, float, float, float, float, float]:
    """Levels and replayed outcomes for a long bracket on ``paths``."""
    window = max(1, min(sim.entry_window, paths.shape[1]))
    entry_lows = paths[:, :window].min(axis=1)
    entry = min(float(np.quantile(entry_lows, sim.entry_fill_prob)), close)
    take_profit = max(float(np.quantile(paths.max(axis=1), 1 - sim.take_profit_prob)), entry)
    stop = min(float(np.quantile(paths.min(axis=1), sim.stop_prob)), entry)
    cost = 2 * sim.cost_bps / 1e4
    filled = entry_lows <= entry
    returns: list[float] = []
    n_tp = n_sl = 0
    for path, is_filled in zip(paths, filled, strict=True):
        if not is_filled:
            continue
        fill_bar = int(np.argmax(path[:window] <= entry))
        exit_price = float(path[-1])
        for price in path[fill_bar:]:
            if price <= stop:
                exit_price, n_sl = stop, n_sl + 1
                break
            if price >= take_profit:
                exit_price, n_tp = take_profit, n_tp + 1
                break
        returns.append(exit_price / entry - 1 - cost)
    n_filled = len(returns)
    p_fill = n_filled / len(paths)
    p_tp = n_tp / n_filled if n_filled else 0.0
    p_sl = n_sl / n_filled if n_filled else 0.0
    ev = float(np.mean(returns)) if returns else -cost
    return entry, take_profit, stop, p_fill, p_tp, p_sl, ev


def plan_from_paths(
    vote: Vote,
    close: float,
    paths: np.ndarray,
    expected_return: float,
    sim: SimulationSettings,
) -> TradePlan:
    """Bracket levels for the voted side; HOLD when the vote or the expected value says so.

    A SELL bracket is the long bracket computed on inverted prices (``1 / price``), whose long
    returns equal the short's returns, then mapped back to prices.
    """
    horizon = paths.shape[1]
    if vote.side == "SELL":
        entry_i, tp_i, sl_i, p_fill, p_tp, p_sl, ev = _long_bracket(1 / close, 1 / paths, sim)
        entry, take_profit, stop = 1 / entry_i, 1 / tp_i, 1 / sl_i
    else:
        entry, take_profit, stop, p_fill, p_tp, p_sl, ev = _long_bracket(close, paths, sim)
    side: Side = vote.side
    if side != "HOLD" and ev <= sim.min_expected_value:
        side = "HOLD"
    return TradePlan(
        side=side,
        close=close,
        entry=entry,
        take_profit=take_profit,
        stop_loss=stop,
        horizon=horizon,
        share_up=vote.share_up,
        share_down=vote.share_down,
        expected_return=expected_return,
        p_fill=p_fill,
        p_take_profit=p_tp,
        p_stop=p_sl,
        expected_value=ev,
    )


def expected_log_return(
    prediction: float, volatility: float, horizon: int, vol_scaled: bool
) -> float:
    """Turn a model prediction into an expected log return over the horizon.

    Vol-scaled labels predict ``return / (vol * sqrt(horizon))``, so scale back up.
    """
    if vol_scaled:
        return prediction * volatility * math.sqrt(horizon)
    return math.log1p(max(prediction, -0.99))


def make_plans(
    ensemble: VotingEnsemble,
    X: pd.DataFrame,
    close: pd.Series,
    volatility: pd.Series,
    shocks: np.ndarray,
    horizon: int,
    vol_scaled: bool,
    sim: SimulationSettings,
) -> pd.DataFrame:
    """One plan per row of ``X`` (indexed like ``close`` and ``volatility``)."""
    rng = np.random.default_rng(sim.seed)
    predictions = ensemble.predict(X)
    rows: list[dict[str, Any]] = []
    for idx, vote, price, vol in zip(
        X.index,
        ensemble.vote(predictions),
        close.loc[X.index],
        volatility.loc[X.index],
        strict=True,
    ):
        drift = expected_log_return(vote.consensus, float(vol), horizon, vol_scaled)
        paths = simulate_paths(float(price), float(vol), drift, horizon, shocks, sim.n_paths, rng)
        plan = plan_from_paths(vote, float(price), paths, drift, sim)
        rows.append(
            {"timestamp": idx[0], "symbol": idx[1], **plan.to_dict(), "dispersion": vote.dispersion}
        )
    return pd.DataFrame(rows).set_index(["timestamp", "symbol"])
