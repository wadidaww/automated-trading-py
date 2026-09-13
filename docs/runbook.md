# Runbook

## Prerequisites

- Futu OpenD gateway running at `127.0.0.1:11111` (or use `mock-opend` for testing)
- `FUTU_ACCOUNT_ID` set in environment or `.env`
- Python 3.11+ with dependencies installed

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
# Output: "ok" or "ok (paper fallback)" or "fail: <error>"
```

## Running backtests

```bash
PYTHONPATH=src python scripts/run_backtest.py
```

## Training models

```bash
PYTHONPATH=src python -m trader.model.trainer --model mean_reversion --symbols 700.HK
```

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
