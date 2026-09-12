"""Structured logging helpers."""

from __future__ import annotations

import logging
from typing import cast

import structlog

_CONFIGURED = False


def _configure_structlog() -> None:
    """Configure structlog once at module level."""
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED:
        return
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer() if __debug__ else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    _CONFIGURED = True


def get_logger(name: str = "trader") -> structlog.stdlib.BoundLogger:
    """Get a named structlog logger.

    Args:
        name: Logger name.

    Returns:
        structlog.stdlib.BoundLogger: Configured logger.
    """
    _configure_structlog()
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
