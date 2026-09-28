# Runbook

## Prerequisites

- Futu OpenD gateway running at `127.0.0.1:11111` (or use `mock-opend` for testing)
- `FUTU_ACCOUNT_ID` set in environment or `.env`
- Python 3.12+ with dependencies installed

## Local development

```bash
cp .env.example .env
# Set FUTU_ACCOUNT_ID in .env

# Start mock gateway (for testing without real OpenD)
docker compose up mock-opend -d

# Run paper trading for 1 hour
PYTHONPATH=src python -m trader --mode paper --duration 3600
```

## Health check

```bash
PYTHONPATH=src python -m trader --health-check
```

Runs four checks in order — `config_load`, `account_id_present` (warn only if
`FUTU_ACCOUNT_ID` is unset), `opend_tcp_probe` (fast TCP reachability), and
`opend_handshake` (futu protocol handshake, bounded at 5s). Dependent checks are
reported as `skip` when their prerequisite failed.

- stderr: structured report — `health_check_context`, one `health_check_check`
  per check (`status=ok|warn|fail|skip`, `detail`, `duration_ms`), and a
  `health_check_complete` summary (`passed`/`warned`/`failed`/`skipped`/`total`).
- stdout: single result line — `ok` or `fail: <check>: <detail>`.
- exit code: `0` when nothing failed (warnings allowed), `1` otherwise.

## Running backtests

```bash
PYTHONPATH=src python scripts/run_backtest.py
```

## Training models

```bash
PYTHONPATH=src python -m trader.model.trainer --model mean_reversion --symbols 700.HK
```

## Tests and quality gates

```bash
ruff check src/ tests/
ruff format --check .
mypy src
PYTHONPATH=src pytest tests/unit tests/integration --cov=src/trader --cov-report=xml --cov-fail-under=64
```

`tests/e2e/` is excluded from CI; coverage gate is 64%.

## Docker deployment

```bash
docker compose up --build
```

## Production deployment

1. Ensure paper trading validation passes
2. Trigger `deploy-prod.yml` workflow
3. Approve the deployment in GitHub Actions environment gate
4. Verify health check after deployment

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ConnectionError: failed to connect` | OpenD not running | Start OpenD or use `mock-opend` |
| Empty account ID | `FUTU_ACCOUNT_ID` not set | Set in `.env` or environment |
| `ok (paper fallback)` | OpenD unreachable | Normal for paper mode without gateway |
| Pipeline exits immediately | Config validation error | Check `config/config.dev.yaml` values |
