"""Market data store with optional SQLite persistence."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import asdict, dataclass


@dataclass(slots=True)
class Candle:
    """Candle record."""

    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class DataStore:
    """In-memory sliding window storage with snapshot support."""

    def __init__(self, window_size: int = 100) -> None:
        self._windows: dict[str, deque[Candle]] = defaultdict(lambda: deque(maxlen=window_size))

    def append(self, symbol: str, candle: Candle) -> None:
        """Append candle by symbol."""
        self._windows[symbol].append(candle)

    def snapshot(self) -> str:
        """Serialize current state to JSON."""
        payload = {key: [asdict(item) for item in items] for key, items in self._windows.items()}
        return json.dumps(payload)

    def restore(self, blob: str) -> None:
        """Restore state from snapshot."""
        data = json.loads(blob)
        self._windows.clear()
        for symbol, candles in data.items():
            for candle in candles:
                self._windows[symbol].append(Candle(**candle))

    def get(self, symbol: str) -> list[Candle]:
        """Get current candles for symbol."""
        return list(self._windows[symbol])
