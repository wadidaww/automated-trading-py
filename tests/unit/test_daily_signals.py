"""Daily signal job: previous-session close → signal → risk → one limit order per session."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

import trader.app.cli as cli
from trader.app import runtime
from trader.adapters.futu.client import FutuClient
from trader.adapters.sim.simulator import PaperSimulator
from trader.jobs import daily_signals as ds
from trader.jobs.daily_signals import DailySignalJob, DailyStateStore, FutuTradingCalendar
from trader.pipeline.execution_stage import OrderReceipt
from trader.app.bootstrap import DefaultPipelineFactory
from trader.config import AppConfig, DailySignalSettings, load_config

SYMBOL = "HK.00700"
BASE = 300.0
WINDOW = 31
# Friday 2026-11-06, 10:00 HKT: continuous session; the previous session is Thursday 11-05.
TODAY = date(2026, 11, 6)
PREVIOUS = date(2026, 11, 5)
OPEN_NS = int(datetime(2026, 11, 6, 2, 0, tzinfo=UTC).timestamp() * 1e9)
PRE_OPEN_NS = int(datetime(2026, 11, 6, 0, 0, tzinfo=UTC).timestamp() * 1e9)  # 08:00 HKT
# Oscillate +/-0.5% then dip 1.5%: a mean-reversion BUY (same shape as tests/helpers/pipeline).
DIP_CLOSE = BASE * 0.985
BUY_CLOSES = [BASE * (1 + pct) for pct in (0.005, -0.005) * 15] + [DIP_CLOSE]


class FixedClock:
    def __init__(self, now_ns: int) -> None:
        self._now_ns = now_ns

    def now_ns(self) -> int:
        return self._now_ns

    def monotonic_ns(self) -> int:
        return self._now_ns


class WeekdayCalendar:
    def __init__(self, holidays: frozenset[date] = frozenset()) -> None:
        self.holidays = holidays

    def trading_days(self, symbol: str, start: date, end: date) -> list[date]:
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        return [d for d in days if d.weekday() < 5 and d not in self.holidays]


class FakeBars:
    """Completed sessions ending at ``last``, plus a still-forming bar for today."""

    def __init__(
        self, closes: list[float], last: date = PREVIOUS, *, today_bar: bool = True
    ) -> None:
        days = pd.bdate_range(end=pd.Timestamp(last), periods=len(closes))
        index = list(days)
        values = list(closes)
        if today_bar:
            index.append(pd.Timestamp(TODAY))
            values.append(closes[-1] * 1.5)  # must be ignored: today's session is not closed
        self.frame = pd.DataFrame({"close": values}, index=pd.DatetimeIndex(index))
        self.calls = 0

    def fetch(self, symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        self.calls += 1
        return self.frame


class FailingBars:
    def fetch(self, symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        raise RuntimeError("opend down")


class _PinnedSimulator(PaperSimulator):
    def __init__(self, price: float) -> None:
        super().__init__()
        self._pinned_price = price

    def advance_price(self, symbol: str) -> float:
        return self._pinned_price


def _client(price: float) -> FutuClient:
    client = FutuClient()
    client._simulator = _PinnedSimulator(price)
    return client


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    base = load_config("config/config.staging.yaml")
    return base.model_copy(
        update={
            "pipeline": base.pipeline.model_copy(update={"data_window_size": WINDOW}),
            "daily": DailySignalSettings(state_file=str(tmp_path / "daily.json")),
            "risk": base.risk.model_copy(update={"kill_switch_file": str(tmp_path / "KILL")}),
        }
    )


def _job(
    config: AppConfig,
    bars: Any,
    *,
    calendar: Any = None,
    broker_price: float = DIP_CLOSE,
    dry_run: bool = False,
    now_ns: int = OPEN_NS,
) -> DailySignalJob:
    return DailySignalJob(
        config,
        DefaultPipelineFactory(config=config, client=_client(broker_price)),
        bars=bars,
        calendar=calendar or WeekdayCalendar(),
        dry_run=dry_run,
        clock=FixedClock(now_ns),
    )


async def test_buy_signal_places_limit_order_at_previous_close(config: AppConfig) -> None:
    job = _job(config, FakeBars(BUY_CLOSES))
    [decision] = await job.run()

    assert decision.action == ds.PLACED, decision
    assert decision.session_date == PREVIOUS.isoformat()
    assert decision.close == pytest.approx(DIP_CLOSE)
    assert decision.side == "BUY"
    assert decision.qty
    assert decision.qty > 0
    assert decision.limit_price == pytest.approx(DIP_CLOSE, rel=0.01)
    assert decision.order_id
    assert len(job.audit_stage.events) == 1
    assert DailyStateStore(config.daily.state_file).last_session(SYMBOL) == PREVIOUS


async def test_second_run_for_same_session_sends_nothing(config: AppConfig) -> None:
    await _job(config, FakeBars(BUY_CLOSES)).run()
    bars = FakeBars(BUY_CLOSES)
    job = _job(config, bars)
    [decision] = await job.run()

    assert decision.action == ds.ALREADY_DONE
    assert bars.calls == 0
    assert not job.audit_stage.events


async def test_dry_run_reports_order_without_sending_or_recording(
    config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job(config, FakeBars(BUY_CLOSES), dry_run=True, now_ns=PRE_OPEN_NS)

    async def _never(_: object) -> OrderReceipt:
        raise AssertionError("dry run must not place orders")

    monkeypatch.setattr(job.execution_stage, "process", _never)
    [decision] = await job.run()

    assert decision.action == ds.DRY_RUN
    assert decision.side == "BUY"
    assert decision.limit_price
    assert not Path(config.daily.state_file).exists()


async def test_flat_closes_place_nothing(config: AppConfig) -> None:
    job = _job(config, FakeBars([BASE] * WINDOW), broker_price=BASE)
    [decision] = await job.run()

    assert decision.action in {ds.NO_SIGNAL, ds.HOLD}
    assert not job.audit_stage.events
    assert not Path(config.daily.state_file).exists()


async def test_risk_reject_is_reported_and_retried_next_run(config: AppConfig) -> None:
    # Broker price 10% above the close: the limit is outside the price band.
    job = _job(config, FakeBars(BUY_CLOSES), broker_price=DIP_CLOSE * 1.10)
    [decision] = await job.run()

    assert decision.action == ds.REJECTED
    assert decision.detail == "price_band"
    assert not Path(config.daily.state_file).exists()


async def test_holiday_is_not_a_trading_day(config: AppConfig) -> None:
    job = _job(config, FakeBars(BUY_CLOSES), calendar=WeekdayCalendar(frozenset({TODAY})))
    [decision] = await job.run()
    assert decision.action == ds.NOT_TRADING_DAY


async def test_config_holiday_is_not_a_trading_day(config: AppConfig) -> None:
    config = config.model_copy(
        update={"session": config.session.model_copy(update={"holidays": [TODAY]})}
    )
    [decision] = await _job(config, FakeBars(BUY_CLOSES)).run()
    assert decision.action == ds.NOT_TRADING_DAY


async def test_stale_bars_are_refused(config: AppConfig) -> None:
    bars = FakeBars(BUY_CLOSES, last=PREVIOUS - timedelta(days=1), today_bar=False)
    [decision] = await _job(config, bars).run()
    assert decision.action == ds.STALE_DATA
    assert "expected 2026-11-05" in decision.detail


async def test_short_history_is_refused(config: AppConfig) -> None:
    [decision] = await _job(config, FakeBars(BUY_CLOSES[-10:])).run()
    assert decision.action == ds.INSUFFICIENT_HISTORY


async def test_orders_wait_for_the_continuous_session(config: AppConfig) -> None:
    bars = FakeBars(BUY_CLOSES)
    [decision] = await _job(config, bars, now_ns=PRE_OPEN_NS).run()
    assert decision.action == ds.MARKET_CLOSED
    assert bars.calls == 0


async def test_unsupported_market_is_refused(config: AppConfig) -> None:
    config = config.model_copy(
        update={"trading": config.trading.model_copy(update={"symbols": ["US.AAPL"]})}
    )
    [decision] = await _job(config, FakeBars(BUY_CLOSES)).run()
    assert decision.action == ds.UNSUPPORTED_MARKET


async def test_bar_download_failure_is_an_error_and_other_symbols_continue(
    config: AppConfig,
) -> None:
    config = config.model_copy(
        update={
            "trading": config.trading.model_copy(update={"symbols": [SYMBOL, "HK.09988"]}),
        }
    )
    decisions = await _job(config, FailingBars()).run()
    assert [d.action for d in decisions] == [ds.ERROR, ds.ERROR]
    assert ds.summarize(decisions)["counts"] == {ds.ERROR: 2}


async def test_failed_send_still_marks_session_done(
    config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job(config, FakeBars(BUY_CLOSES))

    async def _error(intent: Any) -> OrderReceipt:
        return OrderReceipt(client_order_id=intent.client_order_id, order_id="", status="ERROR")

    monkeypatch.setattr(job.execution_stage, "process", _error)
    [decision] = await job.run()

    assert decision.action == ds.ORDER_FAILED
    assert DailyStateStore(config.daily.state_file).last_session(SYMBOL) == PREVIOUS


class _FakeDaysContext:
    def __init__(self, ret: int = 0, data: Any = None) -> None:
        self.ret = ret
        self.data = data
        self.kwargs: dict[str, Any] = {}
        self.closed = False

    def request_trading_days(self, **kwargs: Any) -> tuple[int, Any]:
        self.kwargs = kwargs
        return self.ret, self.data

    def close(self) -> None:
        self.closed = True


def test_futu_calendar_parses_trading_days() -> None:
    ctx = _FakeDaysContext(data=[{"time": "2026-11-06"}, {"time": "2026-11-05"}])
    days = FutuTradingCalendar(context_factory=lambda: ctx).trading_days(SYMBOL, PREVIOUS, TODAY)
    assert days == [PREVIOUS, TODAY]
    assert ctx.kwargs == {"start": "2026-11-05", "end": "2026-11-06", "code": SYMBOL}
    assert ctx.closed


def test_futu_calendar_error_raises_and_closes() -> None:
    ctx = _FakeDaysContext(ret=-1, data="quota")
    with pytest.raises(RuntimeError, match="quota"):
        FutuTradingCalendar(context_factory=lambda: ctx).trading_days(SYMBOL, PREVIOUS, TODAY)
    assert ctx.closed


def test_futu_calendar_crypto_trades_every_day() -> None:
    def _no_context() -> _FakeDaysContext:
        raise AssertionError("crypto needs no OpenD call")

    days = FutuTradingCalendar(context_factory=_no_context).trading_days(
        "CC.BTCUSD", date(2026, 11, 7), date(2026, 11, 8)
    )
    assert days == [date(2026, 11, 7), date(2026, 11, 8)]


def test_state_store_round_trip(tmp_path: Path) -> None:
    store = DailyStateStore(str(tmp_path / "nested" / "state.json"))
    assert store.last_session(SYMBOL) is None
    store.mark(SYMBOL, PREVIOUS)
    store.mark("HK.09988", TODAY)
    assert store.last_session(SYMBOL) == PREVIOUS
    assert DailyStateStore(str(tmp_path / "nested" / "state.json")).last_session("HK.09988") == (
        TODAY
    )


def test_cli_dry_run_requires_daily_signals() -> None:
    with pytest.raises(SystemExit, match="--daily-signals"):
        cli.main(["--dry-run"])


def test_cli_daily_signals_prints_summary_and_fails_on_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    async def _fake_run_daily(mode: str, config_path: str, *, dry_run: bool) -> list[Any]:
        seen.update(mode=mode, config=config_path, dry_run=dry_run)
        return [ds.DailyDecision(SYMBOL, ds.ERROR, detail="boom")]

    monkeypatch.setattr(cli, "run_daily", _fake_run_daily)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--daily-signals", "--dry-run", "--config", "c.yaml"])

    assert exc.value.code == 1
    assert seen == {"mode": "paper", "config": "c.yaml", "dry_run": True}
    assert json.loads(capsys.readouterr().out)["counts"] == {ds.ERROR: 1}


def test_cli_daily_signals_succeeds_without_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run_daily(mode: str, config_path: str, *, dry_run: bool) -> list[Any]:
        return [ds.DailyDecision(SYMBOL, ds.HOLD)]

    monkeypatch.setattr(cli, "run_daily", _fake_run_daily)
    cli.main(["--daily-signals"])


async def test_run_daily_wires_job_and_refuses_live_without_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = yaml.safe_load(Path("config/config.staging.yaml").read_text(encoding="utf-8"))
    payload["pipeline"]["data_window_size"] = WINDOW
    payload["daily"] = {"state_file": str(tmp_path / "daily.json")}
    payload["risk"]["kill_switch_file"] = str(tmp_path / "KILL")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    monkeypatch.setattr(runtime, "HistoricalDataFetcher", lambda **_: FakeBars(BUY_CLOSES))
    monkeypatch.setattr(runtime, "FutuTradingCalendar", lambda **_: WeekdayCalendar())
    monkeypatch.setattr(runtime, "build_client", lambda *_: _client(DIP_CLOSE))
    monkeypatch.setattr(ds, "WallClock", lambda: FixedClock(OPEN_NS))

    [decision] = await runtime.run_daily("paper", str(path), dry_run=True)
    assert decision.action == ds.DRY_RUN

    with pytest.raises(runtime.LiveModeRefusedError):
        await runtime.run_daily("live", str(path), dry_run=True)
