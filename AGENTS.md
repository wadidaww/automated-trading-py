# AGENTS.md

Python algorithmic trading framework for Futu/Futubull OpenAPI. Event-driven asyncio pipeline:
`DataStage → SignalStage → RiskStage → ExecutionStage → AuditStage`. Package `trader` lives under `src/`.

## Commands (source of truth: `.github/workflows/ci.yml`)

```bash
ruff check src/ tests/
ruff format --check .
mypy src
PYTHONPATH=src pytest tests/unit tests/integration --cov=src/trader --cov-report=xml --cov-fail-under=64
```

- CI runs **only** `tests/unit` and `tests/integration`; `tests/e2e/` is excluded (it instantiates the full pipeline). Keep the coverage gate at 64% in mind when adding code.
- All `trader.*` imports are package-qualified; plain `python -m trader ...`, `python scripts/*.py`, and local `pytest` runs need `PYTHONPATH=src` (CI sets `PYTHONPATH: src`, Dockerfile sets `ENV PYTHONPATH=/app/src`).
- pytest is configured with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed except in a couple of existing tests); shared `ohlcv_df` fixture lives in `tests/conftest.py`.

## Environment gotchas

- The repo is Poetry-based (`pyproject.toml`, `poetry.lock`), but **Poetry is not installed** in the dev env and the root `.venv/` only contains `futu-api` (no pytest/ruff/mypy). Run the tools directly from the active conda env (`futu-api` itself is installed via `conda pypi install futu-api`, per README).
- futu-api ships no type stubs: `ignore_missing_imports = true`. `pyproject.toml` sets `strict = true`, but `mypy.ini` takes precedence and is **not** strict.
- The code requires **Python ≥3.12** (PEP 695 generics, e.g. `api/typesafe/payload.py:9`); the Dockerfile and workflows still pin 3.11, where nothing imports and `mypy src` fails to parse.
- Ruff config is duplicated in `.ruff.toml` and `pyproject.toml` (line-length 100, same lint select/ignore). Keep them in sync when editing one.

## Config

- Runtime config is YAML under `config/` (`config.dev.yaml`, `config.staging.yaml`, `config.prod.yaml`), validated by pydantic `AppConfig` in `src/trader/utils/config.py`.
- `${ENV}` placeholders (e.g. `${FUTU_ACCOUNT_ID}`) **are interpolated** at load time via `_render_env_recursive()` called from `load_config()` (`src/trader/utils/config.py:84`). Set the corresponding environment variables or use a `.env` file.
- Entrypoints: `python -m trader --mode paper --duration N --config <path>` (default `config/config.dev.yaml`; also `--health-check`, prints `ok` / `ok (paper fallback)` / `fail: <error>`), `python -m trader.model.trainer --model <type> --symbols <codes>`, `python scripts/run_backtest.py`. Note `scripts/run_backtest.py` ignores its `--config` arg (runs `Backtester().run([])` unconditionally).
- Live/paper runs require the Futu OpenD gateway at `127.0.0.1:11111`. The `mock-opend` compose service is just `python -m http.server 11111` (a stub, not a real gateway). `futu-opend/` holds the actual gateway binaries.

## Repo structure notes

- Pipeline stages are wired through an Abstract Factory (`src/trader/pipeline/factory.py`) against `IStage` (`src/trader/pipeline/base.py`); add new stages there, not by hand-wiring `pipeline.py`.
- Model selection is a registry (`DefaultPipelineFactory._model_builders`, keyed by `model.type` in config): `ensemble`, `gradient_boosting` (lazy import, falls back to `mean_reversion` when sklearn is missing), default `mean_reversion`. `transformer_model` and `lstm_model` exist in `src/trader/model/` but are not wired (`lstm_model` has no `predict()` and does not implement `ISignalModel`).
- `docs/architecture.md`, `docs/runbook.md`, and `docs/api-reference.md` contain overview docs — prefer reading the code for implementation details.
- Git-ignored but present on disk: `futu-opend/`, `src/samples/` (Futu vendor SDK samples, incl. their own `SKILL.md`), `.vscode/`, `.venv/`. Edits inside these are never tracked by git.
- CI: `ci.yml` (lint+test), `backtest.yml`, `model-train.yml`, `paper-trade.yml`, `deploy-prod.yml` (placeholder build + approval gate to `ghcr.io/<repo>:latest`). Dockerfile `ENTRYPOINT python -m trader`.
- `scripts/fetch_historical_data.py` imports `trader.data.fetcher` which does not exist yet — the `model-train.yml` workflow will fail at that step.

## Agents, skills, roadmap

- `docs/production-roadmap.md`: consolidated review findings and the phased plan toward production.
- `.claude/agents/`: `quantitative-developer`, `quant-researcher`, `futu-integration-engineer`, `low-latency-engineer`, `risk-manager` (read-only APPROVE/BLOCK), `trading-sre`.
- `.claude/skills/`: `futu-opend`, `live-trading-safety`, `backtest-validation`, `hot-path-performance`, `add-strategy`. Load the matching skill before editing the area it covers. Any change to risk or execution gets a `risk-manager` review.
