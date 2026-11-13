# AGENTS.md

Python algorithmic trading framework for Futu/Futubull OpenAPI. Event-driven asyncio pipeline:
`DataStage → SignalStage → RiskStage → ExecutionStage → AuditStage`. Package `trader` lives under `src/`.

## Commands (source of truth: `.github/workflows/ci.yml`)

```bash
ruff check src/ tests/
ruff format --check .
mypy src
PYTHONPATH=src pytest tests/unit tests/integration --cov=src/trader --cov-report=xml --cov-fail-under=75
```

- CI runs **only** `tests/unit` and `tests/integration`; `tests/e2e/` is excluded (it instantiates the full pipeline). Keep the coverage gate (75%, set in `ci.yml`) in mind when adding code.
- All `trader.*` imports are package-qualified; plain `python -m trader ...`, `python scripts/*.py`, and local `pytest` runs need `PYTHONPATH=src` (CI sets `PYTHONPATH: src`, Dockerfile sets `ENV PYTHONPATH=/app/src`).
- pytest is configured with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed except in a couple of existing tests); shared `ohlcv_df` fixture lives in `tests/conftest.py`.

## Environment gotchas

- The repo is Poetry-based (`pyproject.toml`, `poetry.lock`), but **Poetry is not installed** in the dev env and the root `.venv/` only contains `futu-api` (no pytest/ruff/mypy). Run the tools directly from the active conda env (`futu-api` itself is installed via `conda pypi install futu-api`, per README).
- futu-api ships no type stubs: `ignore_missing_imports = true`. `pyproject.toml` sets `strict = true`, but `mypy.ini` takes precedence and is **not** strict.
- The code requires **Python ≥3.12** (PEP 695 generics, e.g. `adapters/futu/payload.py:9`). CI, `model-train.yml`, `backtest.yml` and the Dockerfile all use 3.12; `paper-trade.yml` has no setup-python or install step and relies on the runner default.
- Ruff config is duplicated in `.ruff.toml` and `pyproject.toml` (line-length 100, same lint select/ignore). Keep them in sync when editing one.

## Config

- Runtime config is YAML under `config/` (`config.dev.yaml`, `config.staging.yaml`, `config.prod.yaml`), validated by pydantic `AppConfig` in `src/trader/config.py`.
- `${ENV}` placeholders (e.g. `${FUTU_ACCOUNT_ID}`) **are interpolated** at load time via `_render_env_recursive()` called from `load_config()` (`src/trader/config.py`). Set the corresponding environment variables or use a `.env` file.
- Entrypoints: `python -m trader --mode paper --duration N --config <path>` (default `config/config.dev.yaml`; also `--health-check`, which runs `config_load` → `account_id_present` → `opend_tcp_probe` → `opend_handshake`, logs `health_check_check` / `health_check_context` / `health_check_complete` events to stderr, prints `ok` / `fail: <check>: <detail>` on stdout and exits 0/1 — a missing `FUTU_ACCOUNT_ID` only warns), `python -m trader.model.trainer --model <type> --symbols <codes>`, `python scripts/run_backtest.py`. Note `scripts/run_backtest.py` ignores its `--config` arg (runs `Backtester().run([])` unconditionally).
- Live/paper runs require the Futu OpenD gateway at `127.0.0.1:11111`. The `mock-opend` compose service is just `python -m http.server 11111` (a stub, not a real gateway). `futu-opend/` holds the actual gateway binaries.

## Repo structure notes

- Module layout: `__main__.py` only calls `app/cli.py` (argparse); `app/runtime.py` holds the live opt-in gate (`check_live_opt_in`), `build_client`, signal handling and shutdown; `app/health.py` holds `--health-check`; `app/bootstrap.py` (`DefaultPipelineFactory`) is the composition root, the only place config, adapters, models and risk are wired together. The config schema is `config.py`. Layering is enforced by `lint-imports` (contracts in `pyproject.toml`): `core` imports nothing else, only `app` may import `adapters`, `ports` never imports futu, and the live path never imports `research`. The broker contract is `ports/broker.py` (`BrokerClient`) with typed responses in `ports/models.py`; risk, execution and the pipeline depend on it, never on an adapter. `adapters/futu/client.py` (`FutuClient`) is the Futu connection and call layer; per-market behaviour is a `FutuMarket` profile (`adapters/futu/markets.py`): `EquityMarket` in `client.py`, `CryptoMarket` in `crypto.py`. Payload→response mapping is `adapters/futu/parsers.py`, rate limiting `adapters/futu/rate_limit.py`, paper mode `adapters/sim/simulator.py` (`PaperSimulator`, only built for clients allowed to fall back), and `adapters/factory.py` picks the client per market. Quote polling is `marketdata/`. Pure indicators live in `data/indicators.py`; `data/features.py` holds `window_features` (what `DataStage` computes live), `live_feature_frame` (the same features replayed over bar history for training) and the vectorised research feature groups; `data/technical.py` holds the extended indicator library and groups. `patterns/` holds candlestick rules, chart pattern rules (swing pivots, no look-ahead), the CNN `PatternRecognizer` and pattern outcome stats (CLI `python -m trader.patterns`).
- Pipeline stages are wired through an Abstract Factory (`IPipelineFactory` in `src/trader/pipeline/factory.py`, implemented by `src/trader/app/bootstrap.py`) against `IStage` (`src/trader/pipeline/base.py`); add new stages there, not by hand-wiring `pipeline.py`. Pre-trade risk lives in `risk/pre_trade.py` (`PreTradeRisk`); `RiskStage` only adapts it to the pipeline.
- Model selection is a registry (`src/trader/model/registry.py`: `build_model`, `register_model`, keyed by `model.type` in config): `ensemble`, default `mean_reversion`, rule-based `trend` (`model/trend.py`: blended 3/6/12-month return plus Donchian vote; states a target exposure that `PreTradeRisk` sizes by volatility, so it needs `risk.vol_target.enabled` and a long `pipeline.data_window_size`, see `config/config.crypto.trend.yaml`; meant for the daily job, not the tick pipeline), rule-based `sma_trend` (`model/sma_trend.py`: hold while the close is above its `model.trend.sma_window` average, default 200 daily bars; same target-exposure path and daily job as `trend`; paper configs `config/config.crypto.sma.yaml` for BTC and `config/config.hk.nasdaq.sma.yaml` for HK.02834), and fitted-artifact types `gradient_boosting`, `lightgbm`, `linear`, `sklearn_gbm`, `tabular` loaded from `model.path` (startup fails if the file is missing or the model needs features outside the `live` group). Train with `python -m trader.model.trainer` or take `best_model.joblib` from a grid-search run. `transformer_model` and `lstm_model` exist in `src/trader/model/` but are not wired (`lstm_model` has no `predict()` and does not implement `ISignalModel`).
- `docs/architecture.md`, `docs/runbook.md`, and `docs/api-reference.md` contain overview docs — prefer reading the code for implementation details.
- Git-ignored but present on disk: `futu-opend/`, `src/samples/` (Futu vendor SDK samples, incl. their own `SKILL.md`), `.vscode/`, `.venv/`. Edits inside these are never tracked by git.
- CI: `ci.yml` (lint+test), `backtest.yml`, `model-train.yml`, `paper-trade.yml`, `deploy-prod.yml` (placeholder build + approval gate to `ghcr.io/<repo>:latest`). Dockerfile `ENTRYPOINT python -m trader`.
- `scripts/fetch_historical_data.py` downloads bars from OpenD via `trader.data.fetcher` into a CSV cache (`data/raw/<CODE>_<KTYPE>.csv`); it needs a running OpenD, so `model-train.yml` still fails on GitHub runners at that step.
- Research lives in `src/trader/research/` (labels, purged walk-forward, IC/deflated Sharpe scoring, grid search). Run `python -m trader.research.grid_search --spec config/research.example.yaml` (`--synthetic` to try it without data, `--dry-run` to list trials); results go to `data/research/<run>/` (git-ignored).
- Daily signal job: `trader.jobs.daily_signals` (`python -m trader --daily-signals [--dry-run]`, wired in `runtime.run_daily`). Previous session's close → `DataStage`/`SignalStage` → `RiskStage` → LIMIT order at the close; idempotent per session via `daily.state_file`; HK and CC only. Cron example: `deploy/cron/daily-signals.cron`.
- Daily-bar backtest of the target-exposure models: `python -m trader.research.daily_backtest --config <yaml> --bars <csv with close> [--symbol-weight 1.0 --cost-bps 20 --fee 2 --start YYYY-MM-DD]`. It replays closes through `live_feature_frame`, the configured model and `VolTargetSizing` (the code the daily job runs), fills one bar later, and leaves out order caps, lots and the price band. Results of the November 2026 strategy search are in `docs/strategy-research.md`.
- Monte Carlo voting plans: `trader.research.monte_carlo` (randomised `VotingEnsemble`, path simulation, bracket levels) and `trader.research.plan_backtest` (walk-forward execution on real highs/lows; CLI `backtest` / `plan` with `config/montecarlo.example.yaml`). Research only: plans are not wired into order flow.

## Agents, skills, roadmap

- `docs/production-roadmap.md`: consolidated review findings and the phased plan toward production.
- `.claude/agents/`: `quantitative-developer`, `quant-researcher`, `futu-integration-engineer`, `low-latency-engineer`, `risk-manager` (read-only APPROVE/BLOCK), `trading-sre`.
- `.claude/skills/`: `futu-opend`, `live-trading-safety`, `backtest-validation`, `hot-path-performance`, `add-strategy`. Load the matching skill before editing the area it covers. Any change to risk or execution gets a `risk-manager` review.
