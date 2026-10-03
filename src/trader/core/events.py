"""Market-data events flowing from the feed (live push or backtest replay) into the pipeline.

All timestamps are integer nanoseconds. ``exch_ts_ns`` is the exchange/Futu timestamp (UTC epoch
ns); ``recv_ts_ns`` is ``time.monotonic_ns()`` (live) or the simulated clock (backtest) when the
event entered the process. Latency metrics are computed from ``recv_ts_ns`` only.
"""

from __future__ import annotations

from dataclasses import dataclass

from trader.core.orders import Side


@dataclass(slots=True, frozen=True)
class QuoteTick:
    """Top-of-book / last-price update (Futu ``SubType.QUOTE``)."""

    symbol: str
    last: float
    volume: float
    turnover: float
    exch_ts_ns: int
    recv_ts_ns: int


@dataclass(slots=True, frozen=True)
class BookLevel:
    """One price level of the order book."""

    price: float
    qty: float
    orders: int = 0


@dataclass(slots=True, frozen=True)
class OrderBookSnapshot:
    """Level-2 snapshot (Futu ``SubType.ORDER_BOOK``). Bids descending, asks ascending."""

    symbol: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    exch_ts_ns: int
    recv_ts_ns: int

    @property
    def best_bid(self) -> float | None:
        """Best bid price or None when the side is empty."""
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        """Best ask price or None when the side is empty."""
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        """Mid price, or None when either side is empty."""
        if not self.bids or not self.asks:
            return None
        return (self.bids[0].price + self.asks[0].price) / 2.0


@dataclass(slots=True, frozen=True)
class TradeTick:
    """Exchange print (Futu ``SubType.TICKER``). ``aggressor`` is None when unknown/neutral."""

    symbol: str
    price: float
    qty: float
    aggressor: Side | None
    exch_ts_ns: int
    recv_ts_ns: int


@dataclass(slots=True, frozen=True)
class Bar:
    """OHLCV bar (history kline or live ``K_1M``). ``ts_ns`` is the bar *close* time."""

    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float
    ts_ns: int


type MarketEvent = QuoteTick | OrderBookSnapshot | TradeTick | Bar
