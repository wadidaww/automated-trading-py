---
paths:
  - "config/**"
  - "src/trader/utils/config.py"
  - ".env.example"
---

# Config files

Load the `config-change` skill before editing.

- Every YAML under `config/` is validated by `AppConfig` in `src/trader/utils/config.py`. A new
  field goes into the pydantic model first (with a safe default or `Field(...)`), then into every
  config that needs it, then into `tests/integration/test_config_loading.py`.
- `trd_env` stays `SIMULATE` in every committed config. Changing it to `REAL` is the user's call,
  made by hand, never by an agent (the guard hook blocks it).
- Secrets only as `${ENV_VAR}` placeholders (`${FUTU_ACCOUNT_ID}`, `${FUTU_SECURITY_FIRM}`). The
  trade password is referenced by env var *name* (`opend.unlock_password_md5_env`), never a value.
- Symbols are Futu `MARKET.CODE` (`HK.00700`, `US.AAPL`, `CC.ETHUSD`). `normalize_symbol` accepts
  `700.HK`, but write the canonical form. Crypto (`market: CC`) and equity symbols never mix, and
  `market: CC` needs a `trading.crypto` block.
- Notional limits are in the account currency (HKD for HK, USD for US and crypto). The `*_hkd`
  keys are legacy aliases; prefer the plain names in new configs.
- `risk.max_orders_per_30s` must stay below 15 (Futu's `place_order` limit); the validator
  enforces it. `risk.price_band_pct` must be in (0, 0.2).
- Tightening a limit is safe; loosening one (higher notional, more orders, `allow_short: true`,
  `cancel_on_exit: false`) needs the `risk-manager` agent's review and a line in the PR body.
