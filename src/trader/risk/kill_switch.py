"""Latched kill switch: once tripped, no new orders until a human resets it."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from trader.utils.logger import get_logger

logger = get_logger("kill_switch")


class KillSwitch:
    """Latched trading halt.

    Tripped by the daily loss limit, by a signal (SIGUSR1), or by the presence of
    ``trigger_file``. Tripping is idempotent and fires the ``on_trip`` callbacks once (e.g.
    cancel all working orders). Only :meth:`reset` clears it; nothing in the process calls
    reset on its own.
    """

    def __init__(self, trigger_file: str | None = None) -> None:
        self._trigger_file = Path(trigger_file) if trigger_file else None
        self._reason: str | None = None
        self._callbacks: list[Callable[[str], None]] = []

    @property
    def reason(self) -> str | None:
        """Why the switch tripped, or None while trading is allowed."""
        return self._reason

    def on_trip(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked once, with the reason, when the switch trips."""
        self._callbacks.append(callback)

    def is_tripped(self) -> bool:
        """Whether trading is halted. Checks the trigger file on every call."""
        if self._reason is None and self._trigger_file is not None and self._trigger_file.exists():
            self.trip(f"trigger_file:{self._trigger_file}")
        return self._reason is not None

    def trip(self, reason: str) -> None:
        """Halt trading. Later trips keep the first reason."""
        if self._reason is not None:
            return
        self._reason = reason
        logger.error("kill_switch_tripped", reason=reason)
        for callback in self._callbacks:
            try:
                callback(reason)
            except Exception:
                logger.exception("kill_switch_callback_failed", reason=reason)

    def reset(self) -> None:
        """Manually re-enable trading (human action only)."""
        logger.warning("kill_switch_reset", previous_reason=self._reason)
        self._reason = None
