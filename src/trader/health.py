"""``--health-check``: config, account id, OpenD reachability and handshake."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from trader.runtime import build_client
from trader.utils.config import load_config
from trader.utils.logger import get_logger

logger = get_logger("main")

CheckStatus = Literal["ok", "warn", "fail", "skip"]
_DEPENDENT_CHECKS = ("account_id_present", "opend_tcp_probe", "opend_handshake")


@dataclass(slots=True)
class CheckResult:
    """Outcome of a single health check."""

    name: str
    status: CheckStatus
    detail: str
    duration_ms: int


class _Report:
    """Collects, logs and summarises check outcomes."""

    def __init__(self) -> None:
        self.results: list[CheckResult] = []
        self._started = time.monotonic()

    def record(self, name: str, status: CheckStatus, detail: str, begin: float) -> CheckStatus:
        """Record and log one check; returns its status."""
        result = CheckResult(name, status, detail, int((time.monotonic() - begin) * 1000))
        self.results.append(result)
        log = logger.warning if status in ("fail", "warn") else logger.info
        log(
            "health_check_check",
            check=name,
            status=status,
            detail=detail,
            duration_ms=result.duration_ms,
        )
        return status

    def finish(self) -> int:
        """Log the summary, print the result line, and return the exit code (1 on any fail)."""
        failed = [r for r in self.results if r.status == "fail"]
        count = Counter(r.status for r in self.results)
        summary = {
            "passed": count["ok"],
            "warned": count["warn"],
            "failed": count["fail"],
            "skipped": count["skip"],
            "total": len(self.results),
            "duration_ms": int((time.monotonic() - self._started) * 1000),
        }
        names = [r.name for r in failed]
        if failed:
            logger.error("health_check_complete", result="fail", failed_checks=names, **summary)
            print(f"fail: {failed[0].name}: {failed[0].detail}")
            return 1
        logger.info("health_check_complete", result="ok", failed_checks=[], **summary)
        print("ok")
        return 0


async def health_check(config_path: str) -> int:
    """Run the health checks, logging what is and is not in good condition.

    Checks run in order: config load, account id present, OpenD TCP reachability, and the futu
    handshake. Dependent checks are recorded as skipped when their prerequisite failed.

    Returns:
        int: Exit code - 1 when a check failed, 0 otherwise.
    """
    report = _Report()
    begin = time.monotonic()
    try:
        config = load_config(config_path)
    except Exception as exc:
        report.record("config_load", "fail", str(exc), begin)
        for name in _DEPENDENT_CHECKS:
            report.record(name, "skip", "skipped: config_load failed", time.monotonic())
        return report.finish()
    report.record("config_load", "ok", config_path, begin)

    logger.info(
        "health_check_context",
        mode="paper",
        host=config.opend.host,
        port=config.opend.port,
        model_type=config.model.type,
        heartbeat_interval_s=config.opend.heartbeat_interval_s,
        reconnect_max_attempts=config.opend.reconnect_max_attempts,
        config_path=config_path,
    )

    begin = time.monotonic()
    if config.trading.account_id:
        report.record("account_id_present", "ok", "trading.account_id is set", begin)
    else:
        report.record(
            "account_id_present",
            "warn",
            "trading.account_id is empty (FUTU_ACCOUNT_ID unset)",
            begin,
        )

    client = build_client(config, "paper")
    endpoint = f"{config.opend.host}:{config.opend.port}"

    begin = time.monotonic()
    reachable = await client.probe_gateway()
    report.record(
        "opend_tcp_probe",
        "ok" if reachable else "fail",
        endpoint if reachable else f"cannot reach OpenD at {endpoint}",
        begin,
    )

    begin = time.monotonic()
    if not reachable:
        report.record("opend_handshake", "skip", "skipped: opend_tcp_probe failed", begin)
    elif await client.verify_handshake():
        report.record("opend_handshake", "ok", endpoint, begin)
    else:
        report.record("opend_handshake", "fail", f"futu handshake failed at {endpoint}", begin)

    return report.finish()
