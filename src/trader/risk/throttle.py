"""Sliding-window order-rate throttle."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class OrderRateThrottle:
    """Caps orders per second and per 30 s (Futu allows ~15 place_order calls per 30 s).

    ``try_acquire`` either records the send and returns True, or returns False without
    recording anything, so a rejected attempt never consumes budget.
    """

    def __init__(
        self,
        max_per_second: int,
        max_per_30s: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = ((1.0, max_per_second), (30.0, max_per_30s))
        self._clock = clock
        self._sent: deque[float] = deque()

    def try_acquire(self) -> bool:
        """Record one order if both windows have room."""
        now = self._clock()
        while self._sent and now - self._sent[0] >= 30.0:
            self._sent.popleft()
        for window_s, limit in self._limits:
            if sum(1 for ts in self._sent if now - ts < window_s) >= limit:
                return False
        self._sent.append(now)
        return True
