"""Time utilities."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return timezone-aware UTC timestamp.

    Returns:
        datetime: Current UTC datetime.
    """
    return datetime.now(tz=UTC)
