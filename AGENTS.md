# AGENTS.md

Python algorithmic trading framework for Futu/Futubull OpenAPI. Event-driven asyncio pipeline:
`DataStage → SignalStage → RiskStage → ExecutionStage → AuditStage`. Package `trader` lives under `src/`.

## Commands (source of truth: `.github/workflows/ci.yml`)

```bash
ruff check src/ tests/
ruff format --check .
mypy src
pytest tests/unit tests/integration --cov=src/trader --cov-report=xml --cov-fail-under=64
```

- CI runs **only** `tests/unit` and `tests/integration`; `tests/e2e/` is excluded (it instantiates the full pipeline). Keep the coverage gate at 64% in mind when adding code.
- All `trader.*` imports are package-qualified; plain `python -m trader ...` / `python scripts/*.py` need `PYTHONPATH=src` (CI sets `PYTHONPATH: src`, Dockerfile sets `ENV PYTHONPATH=/app/src`).
- pytest is configured with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed except in a couple of existing tests); shared `ohlcv_df` fixture lives in `tests/conftest.py`.

## Environment gotchas

- The repo is Poetry-based (`pyproject.toml`, `poetry.lock`), but **Poetry is not installed** in the dev env and the root `.venv/` only contains `futu-api` (no pytest/ruff/mypy). Run the tools directly from the active conda env (`futu-api` itself is installed via `conda pypi install futu-api`, per README).
- futu-api ships no type stubs: mypy uses `strict = true` with `ignore_missing_imports = true` (both `pyproject.toml` and `mypy.ini`).
- Ruff config is duplicated in `.ruff.toml` and `pyproject.toml` (line-length 100, same lint select/ignore). Keep them in sync when editing one.

## Config

- Runtime config is YAML under `config/` (`config.dev.yaml`, `.staging.yaml`, `.prod.yaml`), validated by pydantic `AppConfig` in `src/trader/utils/config.py`.
- `${ENV}` placeholders (e.g. `${FUTU_ACCOUNT_ID}`) are **not interpolated**: `render_env()` exists but is never wired into `load_config()`, so the literal string is passed through. Don't rely on env substitution working.
- Entrypoints: `python -m trader --mode paper --duration N --config <path>` (also `--health-check`, prints `ok`), `python -m trader.model.trainer --model <type> --symbols <codes>`, `python scripts/run_backtest.py`. Note `scripts/run_backtest.py` ignores its `--config` arg (runs `Backtester().run([])` unconditionally).
- Live/paper runs require the Futu OpenD gateway at `127.0.0.1:11111`. The `mock-opend` compose service is just `python -m http.server 11111` (a stub, not a real gateway). `futu-opend/` holds the actual gateway binaries.

## Repo structure notes

- Pipeline stages are wired through an Abstract Factory (`src/trader/pipeline/factory.py`) against `IStage` (`src/trader/pipeline/base.py`); add new stages there, not by hand-wiring `pipeline.py`.
- `ModelRegistry` (`src/trader/model/registry.py`) loads `data/models/{name}_{version}.json` and falls back to a default `MeanReversionModel`.
- `docs/architecture.md` and `docs/runbook.md` are near-empty stubs — prefer reading the code over these.
- Git-ignored but present on disk: `futu-opend/`, `src/samples/` (Futu vendor SDK samples, incl. their own `SKILL.md`), `.vscode/`, `.venv/`. Edits inside these are never tracked by git.
- CI: `ci.yml` (lint+test), `backtest.yml`, `model-train.yml`, `paper-trade.yml`, `deploy-prod.yml` (placeholder build + approval gate to `ghcr.io/<repo>:latest`). Dockerfile `ENTRYPOINT python -m trader`.