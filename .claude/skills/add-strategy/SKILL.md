---
name: add-strategy
description: Step-by-step recipe for adding a new signal model/strategy to this repo so that it is wired through the factory, shares features with backtest, is sized by risk, and is tested. Load when asked to add or port a strategy, model or signal.
---

# Adding a strategy

1. **Spec first.** Get it from the `quant-researcher` agent or write it yourself:
   - hypothesis and horizon
   - features
   - label
   - entry and exit rules
   - expected bps per trade vs. cost
   - universe
2. **Features.**
   - Add any new ones to the shared feature module (the one `DataStage` uses), as incremental
     updates.
   - Add a reference vectorised version and a property test that the two agree.
   - Never compute features inside the model from wall-clock data.
3. **Model.**
   - Implement `trader.model.base.ISignalModel` in `src/trader/model/<name>.py`.
   - `predict` takes the feature row (not a DataFrame on the hot path) and returns
     `(Signal, confidence)`.
   - `HOLD` means no trade.
   - If confidence feeds sizing, calibrate it.
4. **Register** the builder in `DefaultPipelineFactory._model_builders`
   (`src/trader/pipeline/factory.py`). Unfitted ML models must fail at startup, not on every
   tick. Load the fitted artifact from `model.path`.
5. **Config.** Add parameters under `model:` in `AppConfig`, with defaults, to all three
   `config/*.yaml`.
6. **Tests** (`tests/unit/test_<name>.py`):
   - deterministic signals on the `ohlcv_df` fixture
   - HOLD in flat markets
   - no look-ahead (perturb the future and the decision must not change)
   - serialization round-trip (`tests/integration/test_model_serialization.py` pattern)
7. **Backtest.** Run it through the event-driven backtester with the cost model, and report
   using the `backtest-validation` output template. No live or paper enablement without net-of-cost
   evidence.
8. **Validate:** `ruff check src/ tests/ && ruff format --check . && mypy src && PYTHONPATH=src pytest tests/unit tests/integration --cov=src/trader --cov-fail-under=64`.
