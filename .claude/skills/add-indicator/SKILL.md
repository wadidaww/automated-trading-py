---
name: add-indicator
description: Recipe for adding a technical indicator or feature so live (DataStage) and offline (training, backtest, research) use the same code — trader.data.indicators, FeatureWindow, trader.data.features groups (PR #10), look-ahead and parity tests. Use when asked to add an indicator, feature, or feature group.
---

# Add an indicator or feature

There is exactly one implementation of each formula. Live and offline code call it; nobody
copies it.

## 1. Decide where it lives

| Kind | Location |
|---|---|
| Scalar over a price window, used live | `src/trader/data/indicators.py` (pure, `Sequence[float]` oldest first → `float`) |
| Vectorised research feature over OHLCV bars | a group in `src/trader/data/features.py` `FEATURE_GROUPS` (PR #10) |

Check `test -f src/trader/data/features.py` to see whether PR #10 has landed. Before it, only
`indicators.py` exists and `DataStage` calls it directly.

## 2. Live indicator (`indicators.py`)

- Pure function, no pandas, no I/O, no clock. Return a **neutral value** when the window is too
  short (see `rsi` → 50.0, `bollinger_position` → 0.5, others → 0.0), and guard divide-by-zero.
- Keep it O(window) or better; it runs per tick (see `hot-path-performance`).
- Add the field to `FeatureWindow` in `src/trader/pipeline/data_stage.py` with the same neutral
  default, and compute it in `DataStage.process` (or `window_features` after PR #10).
- If a model consumes it, add it to the model's feature columns and to
  `tests/helpers/features.py:model_features`.

## 3. Research feature group (`features.py`, PR #10)

- Signature `(bars: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame`; call `_require` for the
  columns you need; use `_safe_div` and the shared `_ema` / `_true_range` helpers.
- Rolling windows only look backwards (`rolling`, `shift(+n)`); never `shift(-n)` or centred
  windows.
- Register it in `FEATURE_GROUPS`. A research-only group cannot run live until `DataStage`
  computes it too; say so in the PR.

## 4. Tests

- `tests/unit/test_indicators.py`: short-window neutral value, a hand-computed value, sign or
  saturation behaviour, zero-base guard.
- No look-ahead: change bars after t and assert the value at t is unchanged (pattern in
  `tests/unit/test_features.py` on PR #10).
- Parity: the live value from `DataStage` equals the offline value on the same prices
  (`live_feature_frame` parity test on PR #10).

## 5. Validate

Run the `ci-checks` skill.
