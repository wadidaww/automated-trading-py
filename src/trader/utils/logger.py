"""Structured logging helpers."""

from __future__ import annotations

import logging
from typing import cast

import structlog


def get_logger(name: str = "trader") -> structlog.stdlib.BoundLogger:
    """Create structlog logger.

    Args:
        name: Logger name.

    Returns:
        structlog.stdlib.BoundLogger: Configured logger.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(processors=[structlog.processors.JSONRenderer()])
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
