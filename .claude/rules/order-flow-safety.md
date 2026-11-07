---
paths:
  - "src/trader/risk/**"
  - "src/trader/execution/**"
  - "src/trader/pipeline/risk_stage.py"
  - "src/trader/pipeline/execution_stage.py"
  - "src/trader/pipeline/factory.py"
  - "src/trader/api/**"
  - "src/trader/core/orders.py"
  - "src/trader/core/market_rules.py"
  - "src/trader/core/gateway.py"
  - "src/trader/runtime.py"
  - "src/trader/__main__.py"
---

# Order-flow safety

These files decide whether an order reaches Futu. Load the `live-trading-safety` skill before
editing, and `futu-opend` for anything under `src/trader/api/`.

- Every new branch fails closed: missing or stale quote, position, portfolio, lot size or limit
  data rejects the order with a reason code. No default quantity, price or portfolio value.
- `Signal.HOLD` never becomes an order. Map `BUY` and `SELL` explicitly.
- Exposure checks count working (open) orders as well as fills.
- Do not loosen `check_live_opt_in` in `src/trader/runtime.py`. Live needs `--mode live`,
  `trd_env: REAL`, `TRADER_LIVE_CONFIRM=1` and a numeric account id, and a REAL config without
  `--mode live` is refused too.
- Live mode never falls back to `PaperSimulator`. An unreachable OpenD in live mode is an error.
- The kill switch (`src/trader/risk/kill_switch.py`) is latched: only a human resets it. Never add
  an automatic reset.
- Blocking futu-api calls run off the event loop. SDK push handlers only hand off with
  `loop.call_soon_threadsafe`.
- Add a test for the reject path, not only the happy path, using the fakes in
  `tests/helpers/fakes.py` (`FakeQuoteContext`, `FakeTradeContext`, `FakeCryptoTradeContext`).
- Before opening a PR, ask the `risk-manager` agent to review the diff and include its verdict.
