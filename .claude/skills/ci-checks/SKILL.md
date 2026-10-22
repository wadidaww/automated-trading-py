---
name: ci-checks
description: Run this repo's CI gates locally (ruff lint, ruff format, mypy, pytest with the coverage gate) exactly as .github/workflows/ci.yml does, and report results honestly. Use before committing, before opening a PR, or when asked to check, lint, type-check or test.
---

# Run the CI gates locally

`.github/workflows/ci.yml` is the source of truth. Re-read it if this page and the workflow
disagree; the workflow wins.

## 1. Pick the runner

- Poetry env present (`poetry env info -p` prints a path): prefix each command with
  `poetry run`. This matches CI and has the locked versions.
- Otherwise use the tools on `PATH` from the active conda env (see `AGENTS.md`). Say in your
  report that versions may differ from the lock. Ruff versions disagree on where a `noqa` must
  sit and on formatting Markdown, so check with the locked one when `uvx` is available:
  `uvx ruff@0.4.10 check src/ tests/` and `uvx ruff@0.4.10 format --check .` (version from
  `poetry.lock`).
- Always set `PYTHONPATH=src`.

## 2. Run, in CI order

```bash
export PYTHONPATH=src
ruff check src/ tests/
ruff format --check .
mypy src
pytest tests/unit tests/integration --cov=src/trader --cov-report=xml --cov-fail-under=75
```

Fast loop while iterating: `pytest tests/unit/test_<area>.py -q -x` then the full run at the end.

## 3. Fixing failures

- `ruff format --check` fails: run `ruff format <files you changed>`; do not reformat unrelated
  files.
- `ruff check`: fix the code. Add a `# noqa: <CODE> - <reason>` only with a reason, as in
  `src/trader/utils/config.py`.
- `mypy`: fix the types. Do not add `# type: ignore` without an error code and a reason.
- Coverage under 75%: add tests for the code you added. Never lower the gate or skip tests.
- Collection errors with `No module named 'futu'` (or `yaml`, `pydantic`) mean the environment
  is incomplete, not that the code is broken: install the deps (`poetry install --with dev`, or
  `pip install futu-api` into the active env) and rerun. Do not report them as test failures.

## 4. Report

List each gate as pass or fail, the pytest summary line (passed/failed, coverage %), and paste the
first failure verbatim. If a gate could not run (tool missing), say so; never claim it passed.
