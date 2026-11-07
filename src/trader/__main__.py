"""CLI entrypoint for futu trader."""

from __future__ import annotations

import argparse
import asyncio
import json

from trader.health import health_check
from trader.jobs.daily_signals import FAILURE_ACTIONS, summarize
from trader.runtime import LiveModeRefusedError, logger, run, run_daily


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
    parser.add_argument(
        "--daily-signals",
        action="store_true",
        help="Run the daily job once: signals from the previous session's close become limit "
        "orders (meant for cron), then exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --daily-signals: compute signals and risk checks but send no orders.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Execute CLI app."""
    args = parse_args(argv)
    if args.health_check:
        raise SystemExit(asyncio.run(health_check(args.config)))
    if args.dry_run and not args.daily_signals:
        raise SystemExit("--dry-run only applies to --daily-signals")
    try:
        if args.daily_signals:
            decisions = asyncio.run(run_daily(args.mode, args.config, dry_run=args.dry_run))
            print(json.dumps(summarize(decisions), indent=2))
            if any(d.action in FAILURE_ACTIONS for d in decisions):
                raise SystemExit(1)
            return
        asyncio.run(run(args.mode, args.duration, args.config))
    except LiveModeRefusedError as exc:
        logger.error("live_mode_refused", reason=str(exc))
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
