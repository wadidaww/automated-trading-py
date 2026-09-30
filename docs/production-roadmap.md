# Production Roadmap

This document is a consolidated review of the codebase as of 2026-09-29. It merges three independent reviews:
- Futu OpenD integration and latency
- quant research and backtesting
- risk and operations

It then turns their findings into a phased plan. `file:line` references point at the code at the time of review.

## Honest scope: what "HFT" means on Futu OpenD

Futu OpenD is a retail broker gateway. The path is OpenD → Futu servers → exchange:
- Quote pushes reach the client in low-ms to tens of ms.
- Order acks take roughly 10–100 ms.
- `place_order` is rate-limited to about 15 per 30 s per account.

So co-located, queue-position HFT is not possible on this venue. The realistic target is a **low-latency intraday / short-horizon system**:
- **< 100 µs p99** internal tick-to-decision
- an order on the wire **< 500 µs** after the push arrives
- edges measured in seconds to minutes: microstructure imbalance, regime-filtered mean reversion, intraday momentum

## Current state summary

| Area | State |
|---|---|
| Market data | Polls `get_market_snapshot` every **45 s** per symbol (`api/quote_poller.py:49-62`). No push subscriptions. Uses local timestamps. |
| Symbols | Configs use `700.HK`. Futu expects `HK.00700`, so every real call fails. The three configs are identical. |
| Orders | Futu `order_id` is never stored (`pipeline/execution_stage.py:46`). Dedupe uses a fresh UUID each time, so it never deduplicates (`execution/order_manager.py:73`). No order/deal push. Any status other than `SUBMITTED` is recorded as REJECTED. |
| Live safety | `allow_paper_fallback=True` even in `--mode live` (`api/client.py:320`, `__main__.py:60`). No `unlock_trade`. An empty account ID falls back to `acc_id=0`. The health check always passes. |
| Risk | `HOLD` above the confidence threshold becomes **SELL** (`pipeline/risk_stage.py:120`). Fails open on portfolio errors (`:150-152`). Falls back to `qty=1`. No lot/tick rounding. Open-order count uses the wrong status strings (`:148`). Daily loss uses `unrealized_pnl`. No kill switch. |
| Research | Backtester returns a **hardcoded equity curve** when it gets no trades (`evaluation/backtester.py:9,27`), and `scripts/run_backtest.py` always passes none. Trainer returns literal metrics (`model/trainer.py:32-34`). No data loader (`trader.data.fetcher` is missing). Notebooks are empty. |
| Models | Volatility scaling mixes units, so thresholds collapse to about ±0.01 (`data_stage.py:113`, `mean_reversion.py:197`). `bb_position` duplicates the z-score. Train/serve feature skew (`data/normalizer.py` vs `DataStage`). The unfitted GradientBoosting model raises on every tick. The ensemble returns BUY on all-zero votes. |
| Costs | No stamp duty, levies, commission, platform fee, spread or slippage modelled anywhere. |
| Build/CI | Fixed: the Dockerfile, `ci.yml`, `backtest.yml` and `model-train.yml` now use Python 3.12 (required by the PEP 695 generics in `api/typesafe/payload.py:9`). Under 3.12 with the locked deps, lint, format, `mypy src` and all unit/integration tests pass with 85% coverage. Still open: `mypy.ini` overrides pyproject and is **not strict**; `paper-trade.yml` has no setup-python or dependency install step, and `model-train.yml` still fails at `scripts/fetch_historical_data.py`. |
| Ops | Prometheus metrics are defined but `start_http_server` is never called. Logs are not JSON in prod (`__debug__` check). No persistence (`aiosqlite` is an unused dependency). No reconciliation. No SIGTERM handling. |

## Target architecture

```
          SDK threads (futu-api)                         asyncio / decision thread
┌───────────────────────────────┐   call_soon_threadsafe   ┌──────────────────────────────────────┐
│ Quote / OrderBook / Ticker     │ ───────────────────────▶ │ Per-symbol conflating slots          │
│ push handlers (minimal work)   │                          │  └▶ incremental features (ring buf)  │
│ TradeOrder / TradeDeal push    │ ──▶ Order & Position     │  └▶ model.predict(row)  (no pandas)  │
│ SysNotify (disconnect)         │     Book (in-memory,     │  └▶ pre-trade risk vs in-mem book    │
└───────────────────────────────┘     persisted)  ◀──────── │  └▶ OrderIntent ─▶ executor queue    │
                                                            └──────────────────────────────────────┘
Executor task (owns trade ctx on dedicated thread; per-API token buckets; remark = client id)
Background: audit (append-only), metrics, logs (orjson, QueueHandler), periodic reconciliation
Backtest: same features/model/risk, replayed events + simulated fill/cost model + injected clock
```

## Phased plan

1. ~~Fix Python 3.12 in the Dockerfile and every workflow; fix the two failing pipeline tests and restore coverage above 64%.~~ Done (tests pass under 3.12, coverage 85%). Remaining: enable `strict = true` in `mypy.ini`.
1. Fix Python 3.12 in the Dockerfile and every workflow. Enable `strict = true` in `mypy.ini`. Fix the two failing pipeline tests and restore coverage above 64%.
2. Live-mode guard:
   - no simulator fallback in live
   - a `trd_env` field in config
   - an explicit opt-in env var
   - `get_acc_list` verification and `unlock_trade` (password from a secret)
   - a numeric `acc_id` is required
3. Futu symbol format (`HK.00700`), normalized at config load.
4. Risk fails closed on missing or stale data:
   - remove the `qty=1` fallback and default portfolio values
   - `HOLD` never becomes an order
   - reject a SELL above `can_sell_qty`
5. Map the full Futu `OrderStatus` set. Store the Futu `order_id`. Send a deterministic client id in `remark`.
6. Round to lot size and tick size. Add a price band against the reference price, a kill switch (latched), and an order-rate throttle.
7. Stop fabricating metrics in the backtester and trainer (fail loudly on no data). Fix `run_backtest.py --config`.

### Phase 1: real connectivity and state
1. Push subscriptions (QUOTE, ORDER_BOOK, TICKER) with a thread-safe handoff into conflating slots. Use exchange timestamps.
2. Trade and deal push handlers drive a validated order state machine and a position book.
3. Per-API token buckets that match Futu's limits, with order priority. Run blocking SDK calls on a dedicated executor.
4. Reconnect on `SysNotify` disconnect and re-subscribe.
5. Durable store (SQLite WAL) for orders, fills, positions and audit. Reconcile at startup and adopt or cancel orphans.
6. Market calendar: HK sessions, lunch break, auctions, holidays. Reject stale quotes.
7. Historical data: `trader.data.fetcher` using `request_history_kline` with paging, stored as Parquet.

### Phase 2: quant core
1. One shared, incremental feature module used by live and backtest. Delete the duplicated normalizer logic. Add a parity test.
2. Event-driven backtester that replays events through the factory's stages, with an injected clock, a simulated execution stage, a `CostModel` (HK/US fees), slippage, latency, and a queue-position fill model.
3. Research workflow: triple-barrier labels, purged or walk-forward CV, a trial registry, and reports of deflated Sharpe and PBO.
4. Fix the models: consistent volatility units and a floor, RSI filter logic, the ensemble argmax bug, calibrated confidence. Kelly is fed μ/σ² rather than raw confidence.
5. Microstructure features (order-book imbalance, microprice, signed trade flow, VPIN), regime detection, portfolio construction (shrinkage covariance, turnover penalty).
6. TCA: implementation shortfall, markouts, and attribution by alpha, cost and timing.

### Phase 3: performance and operations
1. Hot path free of pandas and pydantic; numpy ring buffers or numba kernels; uvloop; `gc.freeze()`; pre-bound metric labels; logging off-path.
2. Latency histograms for recv→decide, decide→sent, sent→ack and ack→fill. Benchmarks in `benchmarks/` with a p99 budget enforced in CI.
3. Prometheus server and alerts (disconnect, reject spike, loss-limit proximity, kill switch). JSON logs. A real health check.
4. Environment-specific configs, pinned and hashed dependencies, a slim runtime image (torch moved to a training image), immutable image tags, and a deploy freeze during market hours.
5. Optional: talk to OpenD's TCP/protobuf protocol directly (Rust/pyo3) to skip the SDK's pandas decoding. Do this only if measurements show SDK overhead dominates.

## Agent and skill support

These live under `.claude/` and are used by Claude Code for this work.

| Agent | Role |
|---|---|
| `quantitative-developer` | Implements features, models, sizing and the backtester in one code path |
| `quant-researcher` | Skeptical review of edges, labels, validation and costs (read-mostly) |
| `futu-integration-engineer` | OpenD subscriptions, orders, pushes, reconnect, rate limits |
| `low-latency-engineer` | Hot-path optimisation with benchmarks |
| `risk-manager` | Read-only APPROVE/BLOCK review of anything touching order flow |
| `trading-sre` | CI/CD, Docker, config, secrets, observability, runbooks |

| Skill | Use |
|---|---|
| `futu-opend` | Correct futu-api usage, symbols, statuses, limits, env safety |
| `live-trading-safety` | Order-flow invariants and diff checklist |
| `backtest-validation` | Honest backtests: fills, costs, bias checks, DSR/PBO |
| `hot-path-performance` | Latency budget, rules, measurement |
| `add-strategy` | Recipe for adding a model via the factory |
