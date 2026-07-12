"""Real-time market data feed."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from threading import Lock


@dataclass(slots=True)
class Tick:
    """Tick payload."""

    symbol: str
    price: float


class MarketDataFeed:
    """Thread-safe in-memory tick feed with bounded buffers."""

    def __init__(self, maxlen: int = 1000) -> None:
        self._buffers: dict[str, deque[Tick]] = defaultdict(lambda: deque(maxlen=maxlen))
        self._subs: set[str] = set()
        self._lock = Lock()

    def subscribe(self, symbols: Iterable[str]) -> None:
        """Subscribe symbols.

        Args:
            symbols: Symbol iterable.
        """
        with self._lock:
            self._subs.update(symbols)

    def unsubscribe(self, symbols: Iterable[str]) -> None:
        """Unsubscribe symbols."""
        with self._lock:
            for symbol in symbols:
                self._subs.discard(symbol)

    def push(self, tick: Tick) -> None:
        """Push a tick if symbol subscribed."""
        with self._lock:
            if tick.symbol in self._subs:
                self._buffers[tick.symbol].append(tick)

    def window(self, symbol: str) -> list[Tick]:
        """Get current symbol window."""
        with self._lock:
            return list(self._buffers[symbol])
