from __future__ import annotations

import pytest
from structlog.testing import capture_logs

import trader.__main__ as cli
from trader.api.client import FutuClient
from trader.utils.config import AppConfig


def _make_config(account_id: str = "123") -> AppConfig:
    """Build a minimal valid application config for health check tests.

    Args:
        account_id: Value for trading.account_id.

    Returns:
        AppConfig: Populated config.
    """
    return AppConfig(
        opend={"host": "127.0.0.1", "port": 11111},
        trading={
            "account_id": account_id,
            "symbols": ["700.HK"],
            "max_position_notional_hkd": 75000,
            "max_portfolio_notional_hkd": 750000,
            "max_daily_loss_hkd": 15000,
            "max_open_orders": 15,
            "concentration_limit_pct": 0.25,
            "signal_cooldown_s": 45,
        },
        model={
            "type": "mean_reversion",
            "path": "data/models/model.pkl",
            "hot_reload": False,
            "confidence_threshold": 0.6,
        },
        pipeline={"data_window_size": 120, "feature_interval": "5m", "queue_maxsize": 1000},
        logging={
            "level": "info",
            "format": "json",
            "file": "logs/trader.log",
            "rotate_bytes": 104857600,
        },
        metrics={"prometheus_port": 9090},
    )


def _stub_connectivity(monkeypatch: pytest.MonkeyPatch, *, probe: bool, handshake: bool) -> None:
    """Replace the probe and handshake methods with deterministic stubs.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        probe: Value returned by the TCP probe.
        handshake: Value returned by the handshake verification.
    """

    async def _probe(self: FutuClient) -> bool:
        return probe

    async def _handshake(self: FutuClient, timeout_s: float = 5.0) -> bool:
        return handshake

    monkeypatch.setattr(FutuClient, "probe_gateway", _probe)
    monkeypatch.setattr(FutuClient, "verify_handshake", _handshake)


def _checks(logs: list[dict[str, object]]) -> list[tuple[str, str]]:
    """Extract (check, status) pairs from captured health check logs.

    Args:
        logs: Captured structlog events.

    Returns:
        list[tuple[str, str]]: Ordered check/status pairs.
    """
    return [
        (str(entry["check"]), str(entry["status"]))
        for entry in logs
        if entry["event"] == "health_check_check"
    ]


async def test_health_check_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """All checks pass: stdout is a single `ok` line and the exit code is 0."""
    monkeypatch.setattr(cli, "load_config", lambda _path: _make_config())
    _stub_connectivity(monkeypatch, probe=True, handshake=True)

    with capture_logs() as logs:
        code = await cli.health_check("config/config.dev.yaml")

    assert code == 0
    assert capsys.readouterr().out.strip() == "ok"
    assert _checks(logs) == [
        ("config_load", "ok"),
        ("account_id_present", "ok"),
        ("opend_tcp_probe", "ok"),
        ("opend_handshake", "ok"),
    ]
    summary = next(entry for entry in logs if entry["event"] == "health_check_complete")
    assert summary["result"] == "ok"
    assert summary["total"] == 4
    assert summary["failed"] == 0


async def test_health_check_unreachable_gateway(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unreachable OpenD fails the probe, skips the handshake, and exits 1."""
    monkeypatch.setattr(cli, "load_config", lambda _path: _make_config())
    _stub_connectivity(monkeypatch, probe=False, handshake=True)

    with capture_logs() as logs:
        code = await cli.health_check("config/config.dev.yaml")

    assert code == 1
    out = capsys.readouterr().out
    assert out.startswith("fail: opend_tcp_probe: ")
    assert _checks(logs) == [
        ("config_load", "ok"),
        ("account_id_present", "ok"),
        ("opend_tcp_probe", "fail"),
        ("opend_handshake", "skip"),
    ]
    summary = next(entry for entry in logs if entry["event"] == "health_check_complete")
    assert summary["result"] == "fail"
    assert summary["failed_checks"] == ["opend_tcp_probe"]


async def test_health_check_handshake_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reachable port with a failing futu handshake reports fail and exits 1."""
    monkeypatch.setattr(cli, "load_config", lambda _path: _make_config())
    _stub_connectivity(monkeypatch, probe=True, handshake=False)

    with capture_logs() as logs:
        code = await cli.health_check("config/config.dev.yaml")

    assert code == 1
    assert capsys.readouterr().out.startswith("fail: opend_handshake: ")
    assert ("opend_handshake", "fail") in _checks(logs)


async def test_health_check_account_id_warning_is_not_fatal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing account id is reported as warn but keeps the exit code at 0."""
    monkeypatch.setattr(cli, "load_config", lambda _path: _make_config(account_id=""))
    _stub_connectivity(monkeypatch, probe=True, handshake=True)

    with capture_logs() as logs:
        code = await cli.health_check("config/config.dev.yaml")

    assert code == 0
    assert capsys.readouterr().out.strip() == "ok"
    assert ("account_id_present", "warn") in _checks(logs)


async def test_health_check_config_load_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broken config fails immediately and skips every dependent check."""

    def _broken_config(_path: str) -> AppConfig:
        raise ValueError("bad yaml")

    monkeypatch.setattr(cli, "load_config", _broken_config)

    with capture_logs() as logs:
        code = await cli.health_check("missing.yaml")

    assert code == 1
    assert capsys.readouterr().out.startswith("fail: config_load: ")
    assert _checks(logs) == [
        ("config_load", "fail"),
        ("account_id_present", "skip"),
        ("opend_tcp_probe", "skip"),
        ("opend_handshake", "skip"),
    ]
