"""CLI entrypoint for futu trader."""

from __future__ import annotations

import argparse
import asyncio

from trader.health import health_check
from trader.runtime import LiveModeRefusedError, logger, run


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


def main() -> None:
    """Execute CLI app."""
    args = parse_args()
    if args.health_check:
        raise SystemExit(asyncio.run(health_check(args.config)))
    try:
        asyncio.run(run(args.mode, args.duration, args.config))
    except LiveModeRefusedError as exc:
        logger.error("live_mode_refused", reason=str(exc))
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
