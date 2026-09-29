---
name: live-trading-safety
description: Safety invariants and pre-merge checklist for any change that can affect order flow — risk stage, execution, order manager, config limits, startup/shutdown, live mode. Load before editing src/trader/risk, src/trader/pipeline/risk_stage.py, execution_stage.py, src/trader/execution, __main__.py, or config/*.yaml, and use it to review such diffs.
---

# Live trading safety

A bug in order flow costs real money in milliseconds. Each invariant below has to be enforced
in code and covered by a test.

## Invariants
1. **Explicit live.** REAL trading requires all of:
   - `--mode live`
   - a config whose `trd_env: REAL`
   - an opt-in env var (e.g. `TRADER_LIVE_CONFIRM=1`)
   - `acc_id` verified via `get_acc_list`

   Otherwise refuse and exit non-zero. **There is no paper/simulator fallback in live mode.**
2. **Fail closed.** Missing, stale or error data rejects: portfolio, positions, quote older than
   N seconds, or no lot size. No default sizes (`qty=1`) and no default portfolio values.
3. **Signals are explicit.** `HOLD` never becomes an order. Map `BUY` and `SELL` explicitly and
   reject anything else.
4. **Exposure includes working orders.** Limits are checked against filled + working (open)
   notional, updated synchronously when the order is sent, before the ack arrives.
5. **Pre-trade checks** (all configurable, all tested):
   - max order qty and notional
   - per-symbol and gross notional
   - concentration
   - max open orders
   - order rate (per second and per 30s, under Futu's limit)
   - price band vs. reference (e.g. ±2% of mid)
   - lot and tick validity
   - SELL ≤ `can_sell_qty` unless shorting is explicitly enabled
6. **Loss limits.** Daily P&L = realised (from fills) + unrealised (marks) − start-of-day
   baseline. A breach trips the kill switch.
7. **Kill switch.**
   - Cancel all working orders and block new ones.
   - Triggered by the loss limit, a file or signal (SIGUSR1), or an endpoint.
   - It is latched: only a human resets it.
8. **Idempotency.**
   - A deterministic client order id per intent, sent in `remark`.
   - Futu `order_id` persisted.
   - Retries never double-send.
9. **State and recovery.**
   - Orders, fills and positions are persisted (SQLite/Parquet append-only).
   - On startup, reconcile with the broker, then adopt or cancel orphans before the first
     decision.
10. **Session awareness.** HK hours (09:30–12:00 and 13:00–16:00 HKT), pre-open and closing
    auction (CAS) rules, holidays, halts. No continuous-session order types in auctions.
11. **Shutdown.** SIGTERM leads to: stop new orders, then apply the cancel-on-exit policy, then
    flush the audit log, then exit.
12. **Audit.** A durable append-only record of every signal, risk decision (with reason), order,
    ack, fill, cancel and reject, with exchange and local timestamps.

## Review checklist for a diff
- [ ] Can this change send an order it could not send before? Under what conditions?
- [ ] Is every new error path fail-closed, with a metric and log (reason code)?
- [ ] Are the limits read from config and different per env (dev/staging/prod)?
- [ ] Is there a unit test for the reject path, not just the happy path?
- [ ] Are secrets absent from logs and repr?
- [ ] Were `ruff`, `mypy src` and `pytest tests/unit tests/integration` run?

When in doubt, get the `risk-manager` agent to review the diff.
