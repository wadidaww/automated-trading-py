---
name: config-change
description: Safely add or change a configuration field or risk/trading limit — pydantic AppConfig in src/trader/utils/config.py, the YAMLs under config/, env-var placeholders, validators, and config loading tests. Use when editing config/*.yaml, adding a setting, or changing limits.
---

# Change configuration

## Files

- Schema: `src/trader/utils/config.py` (`AppConfig` and its sections: `opend`, `trading`
  (+ `trading.crypto`), `risk`, `storage`, `session`, `model`, `pipeline`, `logging`, `metrics`).
- Configs: `config/config.dev.yaml` (currently crypto `CC.ETHUSD`), `config.crypto.dev.yaml`,
  `config.staging.yaml` and `config.prod.yaml` (HK `HK.00700`). PR #10 adds
  `research.example.yaml` and `montecarlo.example.yaml`, which are research specs, not `AppConfig`.
- Tests: `tests/integration/test_config_loading.py`.

## Adding a field

1. Add it to the right pydantic section with a type and either a safe default or `Field(...)`
   (required). A wrong default that silently sizes orders is worse than a required field: see
   `CryptoSettings.qty_step`.
2. Add a validator if a bad value could cause harm (pattern: `RiskSettings._check_limits`).
3. Add it to every YAML where the default is not right, with a short comment on units.
4. Read it through `AppConfig` and pass it in via `DefaultPipelineFactory`; do not read YAML or
   env vars elsewhere.
5. Test: loads from each config, rejects a bad value.

## Changing a limit

- State the direction in the PR: tighter (safe) or looser (needs `risk-manager` review).
- Limits should differ by environment: dev and staging tighter than or equal to prod.
- Never set `trd_env: REAL`, never remove the `< 15` order-rate validator, never put a secret
  value in YAML. Secrets are `${ENV_VAR}` placeholders resolved by `load_config`.

## Check

```bash
for f in config/config*.yaml; do PYTHONPATH=src python -c "from trader.utils.config import load_config; load_config('$f'); print('ok', '$f')"; done
```

`${FUTU_ACCOUNT_ID}` may be unset locally; export a dummy like `FUTU_ACCOUNT_ID=0` for this check
only. Then run the `ci-checks` skill.
