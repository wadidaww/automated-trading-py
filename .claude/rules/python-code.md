---
paths:
  - "src/**/*.py"
  - "scripts/**/*.py"
---

# Python code in `src/` and `scripts/`

- Python 3.12+. PEP 695 generics and `from __future__ import annotations` are already in use;
  match the module you are in.
- Imports are package-qualified (`from trader.data import indicators`), never relative or
  `src.trader`. Running anything directly needs `PYTHONPATH=src`.
- Line length 100. Ruff config is duplicated in `.ruff.toml` and `pyproject.toml`; edit both.
- Every public function has type hints and a short docstring (Google style with `Args:` /
  `Returns:` / `Raises:` where the module already uses it). `mypy src` must pass.
- Log with `trader.utils.logger.get_logger(name)` and structured events
  (`logger.info("event_name", key=value)`), never `print` in `src/` (CLI scripts may print results).
- Money is integer minor units where the module already does so (`trader.utils.maths.to_minor_units`).
  Name variables with their units (`notional_minor`, `vol_annual`).
- Strategy and feature code never reads wall-clock time. Use the event timestamp or the clock in
  `trader.core.clock`, so backtest replay is deterministic.
- Wire new stages and models through `trader.pipeline.factory.DefaultPipelineFactory`, not by
  hand in `pipeline.py`.
- One feature implementation: live (`DataStage`) and offline code share `trader.data.indicators`
  (and `trader.data.features` once PR #10 lands). Never copy a formula.
- Do not add a dependency without saying why in the PR; add it with Poetry so `poetry.lock` stays
  in sync.
