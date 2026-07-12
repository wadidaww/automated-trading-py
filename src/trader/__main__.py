"""CLI entrypoint for futu trader."""

from __future__ import annotations

import argparse
import asyncio

from trader.pipeline.pipeline import TradingPipeline


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        argparse.Namespace: Parsed args.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="paper", help="Trading mode: paper or live.")
    parser.add_argument(
        "--duration", type=int, default=5, help="Duration to run the pipeline in seconds."
    )
    parser.add_argument(
        "--health-check", action="store_true", help="Perform a health check and exit."
    )
    parser.add_argument(
        "--config", default="config/config.dev.yaml", help="Path to the configuration file."
    )
    return parser.parse_args()


async def run(duration: int) -> None:
    """Run a short-lived pipeline session.

    Args:
        duration: Runtime duration in seconds.
    """
    pipeline = TradingPipeline(queue_maxsize=100)
    await pipeline.start()
    await asyncio.sleep(duration)
    await pipeline.stop()


def main() -> None:
    """Execute CLI app."""
    args = parse_args()
    if args.health_check:
        print("ok")
        return
    asyncio.run(run(args.duration))


if __name__ == "__main__":
    main()
