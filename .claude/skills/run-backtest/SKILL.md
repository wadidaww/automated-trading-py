---
name: run-backtest
description: How to run backtests and research experiments in this repo — scripts/run_backtest.py over a trades CSV, fetching historical bars from OpenD, the grid search and Monte Carlo plan backtest (trader.research, from PR #10), synthetic dry runs, and where results land. Use when asked to backtest, evaluate, grid-search or produce performance numbers.
---

# Run a backtest or research experiment

Read the `backtest-validation` skill before reporting any number. No data means no metrics: say
so instead of inventing them.

## A. Trade-list backtest (on `main` today)

```bash
PYTHONPATH=src python scripts/run_backtest.py --config config/config.staging.yaml --trades trades.csv
```

- The CSV needs `pnl_minor` and `notional_minor` columns (integer minor units).
- Without `--trades` (or with an empty file) the script exits non-zero by design. The nightly
  `backtest.yml` fails this way until there is data. Do not feed it placeholder trades.
- `trader.evaluation.backtester.Backtester.run` returns a metrics dict from the compounded equity
  curve (`trader.evaluation.metrics`).

## B. Research pipeline (needs PR #10 merged: `trader.research`, `trader.data.fetcher`)

Check first: `test -d src/trader/research || echo "PR #10 not merged yet"`.

1. **Data.** Bars come from OpenD into `data/raw/<CODE>_<KTYPE>.csv` (git-ignored):
   ```bash
   PYTHONPATH=src python scripts/fetch_historical_data.py --symbol HK.00700,HK.09988 --start 2016-01-01
   ```
   This needs a running OpenD and uses the history-kline quota. In a container without OpenD, use
   `--synthetic` below and say the result is synthetic.
2. **Grid search** over features × labels × models with purged walk-forward CV, net of costs:
   ```bash
   PYTHONPATH=src python -m trader.research.grid_search --spec config/research.example.yaml --dry-run
   PYTHONPATH=src python -m trader.research.grid_search --spec config/research.example.yaml --max-trials 10
   PYTHONPATH=src python -m trader.research.grid_search --spec config/research.example.yaml --synthetic
   ```
   Output goes to `data/research/<run>/`: `trials.jsonl`, `leaderboard.csv`, `summary.json`,
   `best_model.joblib`. Always `--dry-run` first to see the trial count.
3. **Monte Carlo voting plans** (entry / take-profit / stop from simulated paths):
   ```bash
   PYTHONPATH=src python -m trader.research.plan_backtest backtest --spec config/montecarlo.example.yaml
   PYTHONPATH=src python -m trader.research.plan_backtest plan --spec config/montecarlo.example.yaml
   ```
   Add `--synthetic` to run without the CSV cache. Plans are research output only; nothing feeds
   order flow.
4. **Deploying a winner** is a model change, not a backtest: follow the `add-strategy` skill.
   Only models on the `live` feature group can run in `DataStage`.

## Spec hygiene

- `backtest.cost_bps` must cover real costs (HK stamp duty alone is 10 bps one way).
- `validation.embargo` ≥ the label horizon; `min_train` large enough for the estimator.
- Copy the example spec to a new file for a new experiment instead of editing the example.

## Report

Use the output template in `backtest-validation`: strategy, commit SHA, data range and source
(real or synthetic), universe, gross vs net Sharpe with frequency and n obs, deflated Sharpe,
max drawdown, turnover, bps/trade vs cost/trade, and known limitations.
