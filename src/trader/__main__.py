"""CLI entrypoint for futu trader."""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from typing import Literal

from futu import TrdEnv

from trader.api.client import FutuClient
from trader.api.quote_handler import QuoteHandler
from trader.api.quote_poller import QuotePoller
from trader.pipeline.pipeline import TradingPipeline
from trader.utils.config import AppConfig, load_config
from trader.utils.logger import get_logger

logger = get_logger("main")

CheckStatus = Literal["ok", "warn", "fail", "skip"]


@dataclass(slots=True)
class CheckResult:
    """Outcome of a single health check."""

    name: str
    status: CheckStatus
    detail: str
    duration_ms: int


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        argparse.Namespace: Parsed args.
    """
    parser = argparse.ArgumentParser(description="Futu automated trader")
    parser.add_argument(
        "--mode",
        default="paper",
        choices=["paper", "live"],
        help="Trading mode: paper (Simulate) or live (Real).",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="Duration to run the pipeline in seconds (default: 300).",
    )
    parser.add_argument(
        "--health-check", action="store_true", help="Perform a health check and exit."
    )
    parser.add_argument(
        "--config", default="config/config.dev.yaml", help="Path to the configuration file."
    )
    return parser.parse_args()


def _build_client(config: AppConfig, mode: str) -> FutuClient:
    """Build a FutuClient from config and mode.

    Args:
        config: Application configuration.
        mode: Trading mode (paper or live).

    Returns:
        Configured FutuClient instance.
    """
    account_id = config.trading.account_id
    acc_id = int(account_id) if account_id.isdigit() else None
    return FutuClient(
        host=config.opend.host,
        port=config.opend.port,
        max_retries=config.opend.reconnect_max_attempts,
        heartbeat_interval_s=config.opend.heartbeat_interval_s,
        rate_limit_requests=config.opend.rate_limit_requests,
        rate_limit_window_s=config.opend.rate_limit_window_s,
        trade_market=config.trading.market,
        trd_env=TrdEnv.SIMULATE if mode == "paper" else TrdEnv.REAL,
        acc_id=acc_id,
    )


async def _run_pipeline(client: FutuClient, config: AppConfig, duration: int) -> None:
    """Start the pipeline and quote poller, then run for the requested duration.

    Args:
        client: Connected FutuClient.
        config: Application configuration.
        duration: Runtime duration in seconds.
    """
    pipeline = TradingPipeline(config=config, client=client)
    poller = QuotePoller(
        client=client,
        handler=QuoteHandler(pipeline.input_queue),
        symbols=config.trading.symbols,
        interval_s=float(config.trading.signal_cooldown_s),
    )

    await pipeline.start()
    await poller.start()

    logger.info(
        "pipeline_running",
        duration_s=duration,
        symbols=config.trading.symbols,
        interval_s=config.trading.signal_cooldown_s,
    )

    await asyncio.sleep(duration)
    await _shutdown(poller, pipeline)


async def _shutdown(poller: QuotePoller, pipeline: TradingPipeline) -> None:
    """Stop polling, drain queued events, and stop the pipeline.

    Args:
        poller: Running quote poller.
        pipeline: Running trading pipeline.
    """
    await poller.stop()
    await pipeline.drain()
    await pipeline.stop()


async def run(mode: str, duration: int, config_path: str) -> None:
    """Run the trading pipeline.

    Args:
        mode: Trading mode (paper or live).
        duration: Runtime duration in seconds.
        config_path: Path to configuration file.
    """
    config = load_config(config_path)
    logger.info("config_loaded", config_path=config_path, mode=mode)

    async with _build_client(config, mode) as client:
        logger.info(
            "opend_connected",
            host=config.opend.host,
            port=config.opend.port,
            mode=mode,
            trd_env=client.trd_env,
        )
        await _run_pipeline(client, config, duration)

    logger.info("pipeline_shutdown_complete")


def _log_check(result: CheckResult) -> None:
    """Log one health check outcome.

    Args:
        result: Recorded check outcome.
    """
    payload = {
        "check": result.name,
        "status": result.status,
        "detail": result.detail,
        "duration_ms": result.duration_ms,
    }
    if result.status in ("fail", "warn"):
        logger.warning("health_check_check", **payload)
    else:
        logger.info("health_check_check", **payload)


def _finish(results: list[CheckResult], started: float) -> int:
    """Log the health check summary, print the result line, and pick the exit code.

    Args:
        results: All recorded check outcomes.
        started: Monotonic timestamp taken when the health check began.

    Returns:
        int: 1 when any check failed, otherwise 0.
    """
    failed = [result.name for result in results if result.status == "fail"]
    summary = {
        "passed": sum(1 for result in results if result.status == "ok"),
        "warned": sum(1 for result in results if result.status == "warn"),
        "failed": len(failed),
        "skipped": sum(1 for result in results if result.status == "skip"),
        "total": len(results),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    if failed:
        first = next(result for result in results if result.status == "fail")
        logger.error("health_check_complete", result="fail", failed_checks=failed, **summary)
        print(f"fail: {first.name}: {first.detail}")
        return 1
    logger.info("health_check_complete", result="ok", failed_checks=[], **summary)
    print("ok")
    return 0


async def health_check(config_path: str) -> int:
    """Run the health checks, logging what is and is not in good condition.

    Checks run in order: config load, account id present, OpenD TCP reachability,
    and the futu handshake. Dependent checks are recorded as skipped when their
    prerequisite failed.

    Args:
        config_path: Path to the configuration file.

    Returns:
        int: Exit code - 1 when a check failed, 0 otherwise.
    """
    results: list[CheckResult] = []
    started = time.monotonic()

    def record(name: str, status: CheckStatus, detail: str, begin: float) -> CheckResult:
        result = CheckResult(
            name=name,
            status=status,
            detail=detail,
            duration_ms=int((time.monotonic() - begin) * 1000),
        )
        results.append(result)
        _log_check(result)
        return result

    begin = time.monotonic()
    try:
        config = load_config(config_path)
    except Exception as exc:
        record("config_load", "fail", str(exc), begin)
        for name in ("account_id_present", "opend_tcp_probe", "opend_handshake"):
            record(name, "skip", "skipped: config_load failed", time.monotonic())
        return _finish(results, started)
    record("config_load", "ok", config_path, begin)

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
        record("account_id_present", "ok", "trading.account_id is set", begin)
    else:
        record(
            "account_id_present",
            "warn",
            "trading.account_id is empty (FUTU_ACCOUNT_ID unset)",
            begin,
        )

    client = _build_client(config, "paper")
    endpoint = f"{config.opend.host}:{config.opend.port}"

    begin = time.monotonic()
    reachable = await client.probe_gateway()
    if reachable:
        record("opend_tcp_probe", "ok", endpoint, begin)
    else:
        record("opend_tcp_probe", "fail", f"cannot reach OpenD at {endpoint}", begin)

    begin = time.monotonic()
    if not reachable:
        record("opend_handshake", "skip", "skipped: opend_tcp_probe failed", begin)
    elif await client.verify_handshake():
        record("opend_handshake", "ok", endpoint, begin)
    else:
        record("opend_handshake", "fail", f"futu handshake failed at {endpoint}", begin)

    return _finish(results, started)


def main() -> None:
    """Execute CLI app."""
    args = parse_args()
    if args.health_check:
        raise SystemExit(asyncio.run(health_check(args.config)))
    asyncio.run(run(args.mode, args.duration, args.config))


if __name__ == "__main__":
    main()
