from __future__ import annotations

from pathlib import Path

import pytest

from trader.runtime import LiveModeRefusedError, check_live_opt_in
from trader.risk.kill_switch import KillSwitch
from trader.risk.throttle import OrderRateThrottle
from trader.utils.config import RiskSettings, load_config


def test_kill_switch_is_latched_and_fires_callbacks_once() -> None:
    reasons: list[str] = []
    kill_switch = KillSwitch()
    kill_switch.on_trip(reasons.append)
    assert not kill_switch.is_tripped()
    kill_switch.trip("manual")
    kill_switch.trip("again")
    assert kill_switch.is_tripped()
    assert kill_switch.reason == "manual"
    assert reasons == ["manual"]
    kill_switch.reset()
    assert not kill_switch.is_tripped()


def test_kill_switch_trips_on_trigger_file(tmp_path: Path) -> None:
    trigger = tmp_path / "KILL"
    kill_switch = KillSwitch(str(trigger))
    assert not kill_switch.is_tripped()
    trigger.touch()
    assert kill_switch.is_tripped()
    trigger.unlink()
    assert kill_switch.is_tripped()  # latched: removing the file does not re-enable trading


def test_kill_switch_callback_errors_do_not_propagate() -> None:
    def _boom(_: str) -> None:
        raise RuntimeError("cancel failed")

    kill_switch = KillSwitch()
    kill_switch.on_trip(_boom)
    kill_switch.trip("loss")
    assert kill_switch.is_tripped()


def test_throttle_enforces_both_windows() -> None:
    now = [0.0]
    throttle = OrderRateThrottle(max_per_second=2, max_per_30s=3, clock=lambda: now[0])
    assert throttle.try_acquire()
    assert throttle.try_acquire()
    assert not throttle.try_acquire()  # per-second
    now[0] = 1.5
    assert throttle.try_acquire()
    now[0] = 3.0
    assert not throttle.try_acquire()  # per-30s
    now[0] = 30.5
    assert throttle.try_acquire()


def test_risk_settings_reject_futu_rate_limit() -> None:
    with pytest.raises(ValueError, match="max_orders_per_30s"):
        RiskSettings(max_orders_per_30s=15)


def test_configs_use_futu_symbols_and_simulate() -> None:
    for env in ("dev", "staging", "prod"):
        cfg = load_config(f"config/config.{env}.yaml")
        assert cfg.trading.symbols == (["CC.ETHUSD"] if env == "dev" else ["HK.00700"])
        assert cfg.trading.trd_env == "SIMULATE"


@pytest.fixture
def real_config(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("FUTU_ACCOUNT_ID", "123456789012345678")
    cfg = load_config("config/config.dev.yaml")
    cfg.trading.trd_env = "REAL"
    return cfg


def test_paper_mode_is_allowed_with_simulate_config() -> None:
    check_live_opt_in(load_config("config/config.dev.yaml"), "paper")


def test_live_mode_requires_real_config() -> None:
    with pytest.raises(LiveModeRefusedError, match="trd_env"):
        check_live_opt_in(load_config("config/config.dev.yaml"), "live")


def test_real_config_requires_live_mode(real_config) -> None:  # noqa: ANN001
    with pytest.raises(LiveModeRefusedError, match="--mode is not live"):
        check_live_opt_in(real_config, "paper")


def test_live_mode_requires_confirm_env(real_config, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("TRADER_LIVE_CONFIRM", raising=False)
    with pytest.raises(LiveModeRefusedError, match="TRADER_LIVE_CONFIRM"):
        check_live_opt_in(real_config, "live")


def test_live_mode_requires_numeric_account(real_config, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("TRADER_LIVE_CONFIRM", "1")
    real_config.trading.account_id = ""
    with pytest.raises(LiveModeRefusedError, match="numeric"):
        check_live_opt_in(real_config, "live")


def test_live_mode_passes_with_every_opt_in(real_config, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("TRADER_LIVE_CONFIRM", "1")
    check_live_opt_in(real_config, "live")
