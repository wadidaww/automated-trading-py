---
paths:
  - ".github/workflows/**"
  - "Dockerfile"
  - "docker-compose.yml"
---

# CI, Docker and deploy

- `ci.yml` is the source of truth for the gates. If you change a command or the coverage
  threshold there, update `AGENTS.md`, `README.md` and the `ci-checks` skill in the same PR.
- Workflows use Python 3.12 and `poetry install --with dev`. `paper-trade.yml` has no
  setup-python step and `mock-opend` is only an HTTP stub, not a gateway.
- No workflow may run `--mode live`, set `TRADER_LIVE_CONFIRM`, or receive trade-password secrets.
  `deploy-prod.yml` keeps its environment approval gate.
- `backtest.yml` (nightly) fails on purpose until it has data: `scripts/run_backtest.py` exits
  non-zero without `--trades`. Do not "fix" it by feeding placeholder data.
- Keep `permissions:` minimal (`contents: read` unless a job needs more).
