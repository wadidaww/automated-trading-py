"""CLI entrypoint for futu trader."""

from __future__ import annotations

import argparse
import asyncio

from futu import TrdEnv

from trader.api.client import FutuClient
from trader.api.quote_handler import QuoteHandler
from trader.api.quote_poller import QuotePoller
from trader.pipeline.pipeline import TradingPipeline
from trader.utils.config import AppConfig, load_config
from trader.utils.logger import get_logger

logger = get_logger("main")


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


async def health_check(config_path: str) -> None:
    """Verify OpenD connectivity and exit.

    Args:
        config_path: Path to configuration file.
    """
    config = load_config(config_path)
    client = _build_client(config, "paper")
    try:
        async with client:
            print("ok" if client.is_connected else "ok (paper fallback)")
    except Exception as exc:
        print(f"fail: {exc}")


def main() -> None:
    """Execute CLI app."""
    args = parse_args()
    if args.health_check:
        asyncio.run(health_check(args.config))
        return
    asyncio.run(run(args.mode, args.duration, args.config))


if __name__ == "__main__":
    main()
