---
name: paper-smoke-test
description: Run the trader end to end in paper mode or run the --health-check, safely and briefly, to confirm a change works outside unit tests. Use when asked to run the app, try it, smoke-test, or check OpenD connectivity. Never live mode.
---

# Paper smoke test

Paper only. `--mode live` is blocked by the guard hook and is never something an agent runs.

Both commands import `futu` (via `trader.api.factory`), so they need `futu-api` installed
(`poetry install` does it). Neither needs a running OpenD. If `futu` is missing, say the smoke
test could not run and rely on `tests/integration/test_pipeline_integration.py`, which drives the
pipeline with fakes.

## Health check

```bash
PYTHONPATH=src python -m trader --health-check --config config/config.dev.yaml
```

Runs `config_load` → `account_id_present` → `opend_tcp_probe` → `opend_handshake`. Prints `ok` or
`fail: <check>: <detail>` on stdout and exits 0/1; structured events go to stderr. In a container
without OpenD, `opend_tcp_probe` failing is expected: report it as "no OpenD here", not as a bug.

## Short paper run

```bash
PYTHONPATH=src timeout 120 python -m trader --mode paper --duration 60 --config config/config.dev.yaml
```

- Always pass a small `--duration` and wrap in `timeout`.
- Equities: without OpenD the client falls back to the local `PaperSimulator` in paper mode.
  Crypto (`market: CC`) has no Futu SIMULATE, so paper always uses the simulator seeded from
  `trading.crypto.paper_*`.
- Do not touch `run/KILL` unless you are testing the kill switch; remove it afterwards and say so.
- Logs are structlog events on stderr (console format in a terminal). Look for
  `signal_rejected`, `order_receipt` and any exception, and check that it ends with
  `cancel_on_exit_done` and `pipeline_shutdown_complete`.

## Report

Command, config, duration, exit code, number of signals / orders / rejects seen, and anything
that could not run (no OpenD, futu-api missing).
