"""Daily signal job: the previous session's close drives one limit order per symbol.

Meant to be started by cron once per trading day while the market is open (see
``deploy/cron/daily-signals.cron``). For every configured symbol it:

1. checks the exchange calendar: today must be a trading day, and the newest completed daily bar
   must be the previous trading session (stale data is refused);
2. skips the symbol if that session was already acted on (a re-run never sends a second order);
3. replays the completed daily closes through ``DataStage`` and ``SignalStage``, so the model sees
   the same features it would in the live pipeline;
4. sends a BUY or SELL through ``RiskStage`` (sizing, limits, price band against the broker's live
   price, kill switch) and ``ExecutionStage`` as a LIMIT order at the previous close. HOLD and
   below-threshold signals place nothing.

``dry_run`` stops after the risk stage and reports the order that would have been sent. Nothing
is cancelled on exit: the orders are meant to rest for the session.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Protocol

import pandas as pd

from trader.marketdata.quote_handler import QuoteEvent
from trader.core.clock import Clock, WallClock
from trader.core.market_rules import HKT, HKSessionCalendar
from trader.core.symbols import CRYPTO_MARKET, market_of
from trader.model.base import Signal
from trader.pipeline.data_stage import FeatureWindow
from trader.pipeline.factory import IPipelineFactory
from trader.config import AppConfig
from trader.utils.logger import get_logger

logger = get_logger("daily_signals")

# Markets whose session hours and dates this job knows. Others are refused, not guessed.
MARKET_TZ: Final[dict[str, timezone]] = {"HK": HKT, CRYPTO_MARKET: UTC}
# How far back to look for the previous trading session (covers Lunar New Year and similar).
_CALENDAR_LOOKBACK_DAYS: Final = 14
_RET_OK: Final = 0

# Decision actions. Only PLACED and ORDER_FAILED record the session as done.
PLACED: Final = "placed"
DRY_RUN: Final = "dry_run"
NO_SIGNAL: Final = "no_signal"
HOLD: Final = "hold"
REJECTED: Final = "rejected"
ORDER_FAILED: Final = "order_failed"
ALREADY_DONE: Final = "already_done"
NOT_TRADING_DAY: Final = "not_trading_day"
MARKET_CLOSED: Final = "market_closed"
UNSUPPORTED_MARKET: Final = "unsupported_market"
STALE_DATA: Final = "stale_data"
INSUFFICIENT_HISTORY: Final = "insufficient_history"
ERROR: Final = "error"
FAILURE_ACTIONS: Final = frozenset({ORDER_FAILED, ERROR})


class BarSource(Protocol):
    """Daily bar download; ``HistoricalDataFetcher`` satisfies it."""

    def fetch(self, symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """Bars indexed by bar time (market-local), oldest first, with a ``close`` column."""
        ...


class TradingCalendar(Protocol):
    """Exchange trading days."""

    def trading_days(self, symbol: str, start: date, end: date) -> list[date]:
        """Trading days for ``symbol``'s market in ``[start, end]``, ascending."""
        ...


class _TradingDaysContext(Protocol):
    def request_trading_days(self, **kwargs: Any) -> tuple[int, Any]: ...

    def close(self) -> None: ...


class FutuTradingCalendar:
    """Trading days from OpenD ``request_trading_days``; crypto trades every day."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        context_factory: Callable[[], _TradingDaysContext] | None = None,
    ) -> None:
        self._context_factory = context_factory or _futu_context_factory(host, port)

    def trading_days(self, symbol: str, start: date, end: date) -> list[date]:
        """Ask OpenD for the market's trading days.

        Raises:
            RuntimeError: When OpenD returns an error (the caller refuses to trade).
        """
        if market_of(symbol) == CRYPTO_MARKET:
            return [start + timedelta(days=i) for i in range((end - start).days + 1)]
        context = self._context_factory()
        try:
            ret, data = context.request_trading_days(
                start=start.isoformat(), end=end.isoformat(), code=symbol
            )
        finally:
            context.close()
        if ret != _RET_OK:
            raise RuntimeError(f"request_trading_days failed for {symbol}: {data}")
        return sorted({_parse_day(row) for row in _rows(data)})


def _futu_context_factory(host: str, port: int) -> Callable[[], _TradingDaysContext]:
    def build() -> _TradingDaysContext:
        from futu import OpenQuoteContext

        context: _TradingDaysContext = OpenQuoteContext(host=host, port=port)
        return context

    return build


def _rows(data: Any) -> list[Any]:
    if isinstance(data, pd.DataFrame):
        return list(data.to_dict("records"))
    return list(data or [])


def _parse_day(row: Any) -> date:
    raw = row.get("time") if isinstance(row, dict) else row
    return date.fromisoformat(str(raw)[:10])


class DailyStateStore:
    """Last session acted on per symbol, as a small JSON file written atomically."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def last_session(self, symbol: str) -> date | None:
        """The session date last acted on for ``symbol``, if any."""
        raw = self._load().get(symbol)
        return date.fromisoformat(raw) if raw else None

    def mark(self, symbol: str, session: date) -> None:
        """Record that ``session`` was acted on for ``symbol``."""
        state = self._load()
        state[symbol] = session.isoformat()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        data: dict[str, str] = json.loads(self._path.read_text(encoding="utf-8"))
        return data


@dataclass(frozen=True, slots=True)
class DailyDecision:
    """What the job did for one symbol."""

    symbol: str
    action: str
    session_date: str = ""
    close: float | None = None
    signal: str = ""
    confidence: float | None = None
    side: str = ""
    qty: float | None = None
    limit_price: float | None = None
    client_order_id: str = ""
    order_id: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form for the run summary."""
        return asdict(self)


class DailySignalJob:
    """Turn the previous session's close into at most one order per symbol."""

    def __init__(
        self,
        config: AppConfig,
        factory: IPipelineFactory,
        bars: BarSource,
        calendar: TradingCalendar,
        *,
        state: DailyStateStore | None = None,
        dry_run: bool = False,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._bars = bars
        self._calendar = calendar
        self._state = state or DailyStateStore(config.daily.state_file)
        self._dry_run = dry_run
        self._clock: Clock = clock or WallClock()
        self._sessions = HKSessionCalendar(
            holidays=frozenset(config.session.holidays),
            half_days=frozenset(config.session.half_days),
        )
        self.data_stage = factory.create_data_stage()
        self.signal_stage = factory.create_signal_stage()
        self.risk_stage = factory.create_risk_stage()
        self.execution_stage = factory.create_execution_stage()
        self.audit_stage = factory.create_audit_stage()

    async def run(self) -> list[DailyDecision]:
        """Process every configured symbol; one symbol's failure never stops the others."""
        decisions: list[DailyDecision] = []
        for symbol in self._config.trading.symbols:
            try:
                decision = await self._run_symbol(symbol)
            except Exception as exc:
                logger.exception("daily_symbol_failed", symbol=symbol)
                decision = DailyDecision(symbol=symbol, action=ERROR, detail=str(exc))
            logger.info("daily_decision", dry_run=self._dry_run, **decision.to_dict())
            decisions.append(decision)
        return decisions

    async def _run_symbol(self, symbol: str) -> DailyDecision:
        tz = MARKET_TZ.get(market_of(symbol))
        if tz is None:
            return DailyDecision(symbol, UNSUPPORTED_MARKET, detail=market_of(symbol))
        now_ns = self._clock.now_ns()
        today = datetime.fromtimestamp(now_ns / 1e9, tz=UTC).astimezone(tz).date()

        days = await asyncio.to_thread(
            self._calendar.trading_days,
            symbol,
            today - timedelta(days=_CALENDAR_LOOKBACK_DAYS),
            today,
        )
        if today not in days or today in self._config.session.holidays:
            return DailyDecision(symbol, NOT_TRADING_DAY, detail=today.isoformat())
        previous = [d for d in days if d < today]
        if not previous:
            return DailyDecision(symbol, STALE_DATA, detail="no previous trading day in calendar")
        session = previous[-1]

        last = self._state.last_session(symbol)
        if last is not None and last >= session:
            return DailyDecision(symbol, ALREADY_DONE, session_date=session.isoformat())
        if not self._dry_run and not self._market_open(symbol, now_ns):
            return DailyDecision(symbol, MARKET_CLOSED, session_date=session.isoformat())

        closes = await self._completed_closes(symbol, today)
        if closes.empty or closes.index[-1] != session:
            seen = closes.index[-1].isoformat() if not closes.empty else "none"
            return DailyDecision(
                symbol,
                STALE_DATA,
                session_date=session.isoformat(),
                detail=f"latest completed bar {seen}, expected {session.isoformat()}",
            )
        window_size = self._config.pipeline.data_window_size
        if len(closes) < window_size:
            return DailyDecision(
                symbol,
                INSUFFICIENT_HISTORY,
                session_date=session.isoformat(),
                detail=f"{len(closes)} bars, need {window_size}",
            )
        return await self._decide(symbol, session, closes.iloc[-window_size:])

    def _market_open(self, symbol: str, now_ns: int) -> bool:
        """Orders are only sent in the continuous session (crypto is always continuous)."""
        if market_of(symbol) == CRYPTO_MARKET:
            return True
        return self._sessions.is_continuous(now_ns)

    async def _completed_closes(self, symbol: str, today: date) -> pd.Series:
        """Daily closes for sessions before ``today`` (today's bar may still be forming)."""
        start = today - timedelta(days=self._config.daily.history_days)
        bars = await asyncio.to_thread(
            self._bars.fetch, symbol, start.isoformat(), today.isoformat()
        )
        if bars.empty:
            return pd.Series(dtype=float)
        closes = bars["close"].astype(float)
        closes.index = [pd.Timestamp(ts).date() for ts in bars.index]
        closes = closes[~closes.index.duplicated(keep="last")].sort_index()
        return closes[[d < today for d in closes.index]].dropna()

    async def _decide(self, symbol: str, session: date, closes: pd.Series) -> DailyDecision:
        features = await self._features(symbol, closes)
        decision = DailyDecision(
            symbol, NO_SIGNAL, session_date=session.isoformat(), close=float(closes.iloc[-1])
        )

        signal = await self.signal_stage.process(features)
        if signal is None:
            return decision
        decision = replace(decision, signal=signal.signal.value, confidence=signal.confidence)
        if signal.signal is Signal.HOLD:
            return replace(decision, action=HOLD)

        before = dict(self.risk_stage.reject_reasons)
        intent = await self.risk_stage.process(signal)
        if intent is None:
            reasons = [
                reason
                for reason, count in self.risk_stage.reject_reasons.items()
                if count > before.get(reason, 0)
            ]
            return replace(decision, action=REJECTED, detail=",".join(reasons))
        decision = replace(
            decision,
            side=intent.side,
            qty=intent.qty,
            limit_price=intent.limit_price,
            client_order_id=intent.client_order_id,
        )
        if self._dry_run:
            return replace(decision, action=DRY_RUN)

        receipt = await self.execution_stage.process(intent)
        await self.audit_stage.process(receipt)
        # Recorded even on failure: an errored send may still have reached the broker, and a
        # second order for the same session is worse than a missed one.
        self._state.mark(symbol, session)
        if receipt.status == "ERROR":
            return replace(decision, action=ORDER_FAILED, detail="placement failed")
        return replace(decision, action=PLACED, order_id=receipt.order_id, detail=receipt.status)

    async def _features(self, symbol: str, closes: pd.Series) -> FeatureWindow:
        """Replay the closes through ``DataStage``; the last window is the decision input."""
        window: FeatureWindow | None = None
        for day, close in closes.items():
            ts = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
            window = await self.data_stage.process(
                QuoteEvent(symbol=symbol, price=float(close), timestamp=ts)
            )
        if window is None:
            raise ValueError(f"no closes for {symbol}")
        return window


def summarize(decisions: list[DailyDecision]) -> dict[str, Any]:
    """Counts per action plus every decision, for the run log and stdout."""
    counts: dict[str, int] = {}
    for decision in decisions:
        counts[decision.action] = counts.get(decision.action, 0) + 1
    return {"counts": counts, "decisions": [d.to_dict() for d in decisions]}
