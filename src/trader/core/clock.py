"""Injectable clocks so the same code runs live and in backtest replay."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """Time source. ``now_ns`` is wall-clock UTC epoch ns; ``monotonic_ns`` is for durations."""

    def now_ns(self) -> int:
        """Current UTC epoch time in nanoseconds."""
        ...

    def monotonic_ns(self) -> int:
        """Monotonic time in nanoseconds (durations, latency, rate limits)."""
        ...


class WallClock:
    """Real time."""

    __slots__ = ()

    def now_ns(self) -> int:
        """Current UTC epoch time in nanoseconds."""
        return time.time_ns()

    def monotonic_ns(self) -> int:
        """Monotonic time in nanoseconds."""
        return time.monotonic_ns()


class SimulatedClock:
    """Clock driven by replayed event timestamps. Never goes backwards."""

    __slots__ = ("_now_ns",)

    def __init__(self, start_ns: int = 0) -> None:
        self._now_ns = start_ns

    def now_ns(self) -> int:
        """Current simulated epoch time in nanoseconds."""
        return self._now_ns

    def monotonic_ns(self) -> int:
        """Simulated time doubles as monotonic time."""
        return self._now_ns

    def advance_to(self, ts_ns: int) -> None:
        """Move time forward to ``ts_ns``; earlier timestamps are ignored."""
        if ts_ns > self._now_ns:
            self._now_ns = ts_ns
