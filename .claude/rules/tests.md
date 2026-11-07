---
paths:
  - "tests/**"
---

# Tests

- CI runs `tests/unit` and `tests/integration` only, with the coverage gate in
  `.github/workflows/ci.yml` (`--cov-fail-under=75`). `tests/e2e/` is not run in CI.
- `asyncio_mode = "auto"`: write `async def test_...` without `@pytest.mark.asyncio`.
- `pythonpath = ["tests"]`, so helpers import as `from helpers import FakeTradeContext, make_order`.
  Reuse `tests/helpers/` (`fakes.py`, `responses.py`, `features.py`, `pipeline.py`) and the
  `ohlcv_df` fixture in `tests/conftest.py` before writing new fakes.
- No test may need a running OpenD or the network. `futu-api` is installed (Poetry, CI) and
  several test modules import it, but nothing may connect: inject the fakes into `FutuClient` /
  `FutuCryptoClient` instead of real contexts.
- Deterministic only: fixed seeds, no `datetime.now()` without `freezegun`, no sleeps.
- For order-flow code, test the reject paths. For features, test no look-ahead (perturb future
  bars, the value at t must not change).
- Never delete, skip or `xfail` a test to get green, and never lower the coverage gate.
