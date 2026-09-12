# Python repo workflow

Use these conventions when working in this repository.

## Environment
- Use the active conda environment for Python execution.
- Do not assume Poetry is installed in the dev environment.
- For direct script/module execution, set `PYTHONPATH=src`.

## Commands
- Lint: `ruff check src/ tests/`
- Format: `ruff format --check .`
- Type check: `mypy src`
- Tests: `pytest tests/unit tests/integration --cov=src/trader --cov-report=xml --cov-fail-under=64`

## Architecture
- Main code lives in `src/trader`.
- Event-driven pipeline: DataStage -> SignalStage -> RiskStage -> ExecutionStage -> AuditStage.
- Keep changes in the appropriate stage or utility; do not bypass the pipeline factory.
- Prefer package-qualified imports and existing repository patterns.

## Safe defaults
- Prefer targeted fixes over large refactors.
- Read relevant tests before editing.
- Validate with the smallest command that checks the changed behavior.
- Preserve async semantics and non-blocking workflow patterns.
