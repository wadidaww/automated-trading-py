"""Crypto (Futu ``CC.*``) support: symbols, rules, client, factory, risk sizing and config."""

from __future__ import annotations

from typing import Any

import pytest
from futu import OrderType, TrdEnv
from pydantic import ValidationError

from trader.api import crypto_client as crypto_module
from trader.api.client import FutuClient, PortfolioResponse
from trader.api.crypto_client import CryptoSdkError, FutuCryptoClient
from trader.api.factory import create_client
from trader.core.clock import SimulatedClock
from trader.core.market_rules import (
    CryptoSessionCalendar,
    SessionPhase,
    round_down_to_step,
    round_to_tick,
    rules_for,
    tick_size,
)
from trader.core.symbols import normalize_symbol
from trader.model.base import Signal
from trader.pipeline.risk_stage import PreTradeLimits, RiskStage
from trader.pipeline.signal_stage import TradeSignal
from trader.risk.kill_switch import KillSwitch
from trader.risk.risk_engine import RiskEngine
from trader.risk.throttle import OrderRateThrottle
from trader.runtime import build_client, check_live_opt_in, LiveModeRefusedError
from trader.utils.maths import to_minor_units_ceil
from trader.utils.config import CryptoSettings, TradingSettings, load_config
from helpers import FakeCryptoTradeContext, FakeQuoteContext, make_stock_info

SYMBOL = "CC.ETHUSD"


def _settings(**overrides: Any) -> CryptoSettings:
    fields: dict[str, Any] = {"qty_step": 0.0001, "min_qty": 0.0001}
    fields.update(overrides)
    return CryptoSettings(**fields)


CRYPTO_CONFIG = "config/config.crypto.dev.yaml"


# --- symbols and market rules -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("CC.ETHUSD", "CC.ETHUSD"), ("cc.btcusd", "CC.BTCUSD"), ("BTCHKD.CC", "CC.BTCHKD")],
)
def test_normalize_crypto_symbol(raw: str, expected: str) -> None:
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["CC.BTC", "CC.BT-USD", "CC."])
def test_crypto_symbol_must_be_a_trading_pair(raw: str) -> None:
    with pytest.raises(ValueError, match="CC codes|must be MARKET"):
        normalize_symbol(raw)


def test_crypto_rules_are_fractional_with_broker_tick() -> None:
    assert rules_for(SYMBOL).fractional_qty
    assert rules_for(SYMBOL).broker_tick
    assert not rules_for("HK.00700").fractional_qty


def test_crypto_tick_requires_broker_value() -> None:
    with pytest.raises(ValueError, match="broker-supplied tick"):
        tick_size(SYMBOL, 2000.0)
    assert tick_size(SYMBOL, 2000.0, tick=0.01) == 0.01


def test_crypto_round_to_tick_is_exact_and_passive() -> None:
    assert round_to_tick(SYMBOL, 2000.129, "BUY", tick=0.01) == 2000.12
    assert round_to_tick(SYMBOL, 2000.121, "SELL", tick=0.01) == 2000.13
    assert round_to_tick(SYMBOL, 0.30000000000000004, "BUY", tick=0.0001) == 0.3


def test_round_down_to_step() -> None:
    assert round_down_to_step(0.123456, 0.0001) == 0.1234
    assert round_down_to_step(0.00009, 0.0001) == 0.0
    assert round_down_to_step(1.0, 0.0) == 0.0
    assert round_down_to_step(0.3, 0.1) == 0.3  # no float drift (0.3 // 0.1 == 2.0)


def test_crypto_calendar_is_always_continuous() -> None:
    calendar = CryptoSessionCalendar()
    saturday_3am = 1_700_000_000_000_000_000
    assert calendar.phase(saturday_3am) is SessionPhase.CONTINUOUS
    assert calendar.is_continuous(0)


# --- config ---------------------------------------------------------------------------------------


def test_crypto_config_loads() -> None:
    cfg = load_config(CRYPTO_CONFIG)
    assert cfg.trading.market == "CC"
    assert cfg.trading.symbols == [SYMBOL]
    assert cfg.trading.trd_env == "SIMULATE"
    assert cfg.trading.max_position_notional == 5000
    assert cfg.risk.max_order_qty == 1.0


def _trading(**overrides: Any) -> TradingSettings:
    fields: dict[str, Any] = {
        "account_id": "1",
        "market": "CC",
        "symbols": [SYMBOL],
        "max_position_notional": 1,
        "max_portfolio_notional": 1,
        "max_daily_loss": 1,
        "max_open_orders": 1,
        "concentration_limit_pct": 0.5,
        "signal_cooldown_s": 1,
        "crypto": {"qty_step": 0.0001, "min_qty": 0.0001},
    }
    fields.update(overrides)
    return TradingSettings(**fields)


def test_legacy_hkd_limit_keys_still_load() -> None:
    fields: dict[str, Any] = {
        "account_id": "1",
        "market": "HK",
        "symbols": ["HK.00700"],
        "max_position_notional_hkd": 10,
        "max_portfolio_notional_hkd": 20,
        "max_daily_loss_hkd": 30,
        "max_open_orders": 1,
        "concentration_limit_pct": 0.5,
        "signal_cooldown_s": 1,
    }
    legacy = TradingSettings.model_validate(fields)
    assert (legacy.max_position_notional, legacy.max_portfolio_notional) == (10, 20)
    assert legacy.max_daily_loss == 30


def test_crypto_and_equity_symbols_cannot_mix() -> None:
    with pytest.raises(ValidationError, match="cannot trade"):
        _trading(symbols=[SYMBOL, "HK.00700"])
    with pytest.raises(ValidationError, match="cannot trade"):
        _trading(market="HK", symbols=[SYMBOL])


def test_crypto_security_firm_is_validated() -> None:
    assert _settings(security_firm="futuinc").security_firm == "FUTUINC"
    assert _settings().security_firm == ""
    with pytest.raises(ValidationError, match="security_firm"):
        _settings(security_firm="FUTUMY")


# --- client factory and client --------------------------------------------------------------------


def test_factory_builds_client_by_market() -> None:
    crypto = load_config(CRYPTO_CONFIG)
    equity = load_config("config/config.staging.yaml")
    assert isinstance(create_client(crypto, "paper"), FutuCryptoClient)
    assert type(create_client(equity, "paper")) is FutuClient
    assert isinstance(build_client(crypto, "paper"), FutuCryptoClient)


def test_factory_live_crypto_never_falls_back_to_paper() -> None:
    client = create_client(load_config(CRYPTO_CONFIG), "live")
    assert isinstance(client, FutuCryptoClient)
    assert client.trd_env == TrdEnv.REAL
    assert client.allow_paper_fallback is False
    assert not client.simulated


async def test_crypto_paper_mode_never_contacts_opend() -> None:
    client = create_client(load_config(CRYPTO_CONFIG), "paper")
    async with client:
        info = await client.get_stock_info(SYMBOL)
        order = await client.place_order(
            SYMBOL, 0.0123, "BUY", order_type="LIMIT", price=info.price, remark="c1"
        )
        portfolio = await client.get_portfolio()
    assert info.lot_size is None
    assert info.tick_size == 0.01
    assert info.qty_step == 0.0001
    assert order.qty == 0.0123
    assert portfolio.total_assets == 100_000.0
    with pytest.raises(RuntimeError, match="trade context"):
        await client.verify_account()


def _live_client(settings: CryptoSettings, **kwargs: Any) -> FutuCryptoClient:
    return FutuCryptoClient(
        settings,
        trd_env=TrdEnv.REAL,
        acc_id=281756479345015383,
        max_retries=1,
        **kwargs,
    )


@pytest.fixture
def fake_crypto_sdk(monkeypatch: pytest.MonkeyPatch) -> type[FakeCryptoTradeContext]:
    FakeCryptoTradeContext.instances = []
    monkeypatch.setattr(crypto_module, "OpenQuoteContext", FakeQuoteContext)
    monkeypatch.setattr(crypto_module, "OpenCryptoTradeContext", FakeCryptoTradeContext)
    return FakeCryptoTradeContext


async def test_live_limit_order_uses_normal_gtc_and_fractional_qty(
    fake_crypto_sdk: type[FakeCryptoTradeContext],
) -> None:
    async with _live_client(_settings(security_firm="FUTUINC")) as client:
        order = await client.place_order(
            SYMBOL, 0.0123, "BUY", order_type="LIMIT", price=2000.5, remark="c1"
        )
    ctx = fake_crypto_sdk.instances[0]
    assert ctx.init_kwargs["security_firm"] == "FUTUINC"
    assert ctx.place_kwargs["order_type"] == OrderType.NORMAL
    assert ctx.place_kwargs["time_in_force"] == "GTC"
    assert ctx.place_kwargs["trd_env"] == TrdEnv.REAL
    assert ctx.place_kwargs["remark"] == "c1"
    assert order.order_id == "123"


async def test_live_market_order_is_ioc(fake_crypto_sdk: type[FakeCryptoTradeContext]) -> None:
    async with _live_client(_settings(security_firm="FUTUINC")) as client:
        await client.place_order(SYMBOL, 0.5, "SELL", order_type="MARKET")
    ctx = fake_crypto_sdk.instances[0]
    assert ctx.place_kwargs["order_type"] == OrderType.MARKET
    assert ctx.place_kwargs["time_in_force"] == "IOC"


async def test_live_order_below_min_qty_is_refused_before_send(
    fake_crypto_sdk: type[FakeCryptoTradeContext],
) -> None:
    async with _live_client(_settings(security_firm="FUTUINC", min_qty=0.01)) as client:
        with pytest.raises(ValueError, match="below min_qty"):
            await client.place_order(SYMBOL, 0.001, "BUY", order_type="LIMIT", price=1.0)
    assert fake_crypto_sdk.instances[0].place_kwargs == {}


async def test_live_requires_security_firm(
    fake_crypto_sdk: type[FakeCryptoTradeContext],
) -> None:
    with pytest.raises(ValueError, match="security_firm"):
        async with _live_client(_settings()):
            pass
    assert fake_crypto_sdk.instances == []


async def test_live_without_crypto_sdk_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto_module, "OpenQuoteContext", FakeQuoteContext)
    monkeypatch.setattr(crypto_module, "OpenCryptoTradeContext", None)
    client = _live_client(_settings(security_firm="FUTUINC"))
    with pytest.raises(CryptoSdkError, match="futu-api >= 10.5.6508"):
        await client.connect()


async def test_live_unreachable_gateway_is_an_error_not_paper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _refuse(**_: object) -> None:
        raise RuntimeError("OpenD unreachable")

    monkeypatch.setattr(crypto_module, "OpenQuoteContext", _refuse)
    client = _live_client(_settings(security_firm="FUTUINC"))
    with pytest.raises(ConnectionError):
        await client.connect()
    assert not client.is_connected


async def test_live_cancel_all_reaches_crypto_context(
    fake_crypto_sdk: type[FakeCryptoTradeContext],
) -> None:
    async with _live_client(_settings(security_firm="FUTUINC")) as client:
        await client.cancel_all_orders()
    assert fake_crypto_sdk.instances[0].cancel_all_calls == 1


async def test_equity_client_rejects_fractional_qty() -> None:
    async with FutuClient(allow_paper_fallback=True, max_retries=0) as client:
        with pytest.raises(ValueError, match="whole number"):
            await client.place_order("HK.00700", 1.5, "BUY", order_type="LIMIT", price=1.0)


def test_live_gate_is_unchanged_for_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUTU_ACCOUNT_ID", "281756479345015383")
    monkeypatch.delenv("TRADER_LIVE_CONFIRM", raising=False)
    cfg = load_config(CRYPTO_CONFIG)
    with pytest.raises(LiveModeRefusedError):  # SIMULATE config + --mode live
        check_live_opt_in(cfg, "live")
    cfg.trading.trd_env = "REAL"
    with pytest.raises(LiveModeRefusedError):  # REAL without --mode live
        check_live_opt_in(cfg, "paper")
    with pytest.raises(LiveModeRefusedError, match="TRADER_LIVE_CONFIRM"):
        check_live_opt_in(cfg, "live")
    monkeypatch.setenv("TRADER_LIVE_CONFIRM", "1")
    check_live_opt_in(cfg, "live")


# --- risk stage -----------------------------------------------------------------------------------


class CryptoFakeClient:
    """Broker stub for crypto: float positions, tick/step from instrument metadata."""

    def __init__(self) -> None:
        self.total_assets = 100_000.0
        self.can_sell = 0.0
        self.tick: float | None = 0.01
        self.qty_step: float | None = 0.0001

    async def get_portfolio(self) -> PortfolioResponse:
        return PortfolioResponse(
            account_id=1,
            total_assets=self.total_assets,
            market_value=0.0,
            cash=self.total_assets,
            available_cash=self.total_assets,
        )

    async def get_positions(self) -> list[Any]:
        from helpers import make_position

        if self.can_sell <= 0:
            return []
        return [
            make_position(SYMBOL, self.can_sell, can_sell_qty=self.can_sell, nominal_price=2000.0)
        ]

    async def list_orders(self) -> list[Any]:
        return []

    async def get_stock_info(self, symbol: str) -> Any:
        info = make_stock_info(symbol, 2000.0)
        return info.model_copy(
            update={
                "lot_size": None,
                "tick_size": self.tick,
                "qty_step": self.qty_step,
                "min_qty": 0.0001,
            }
        )


def _stage(client: CryptoFakeClient, **limits: Any) -> RiskStage:
    engine = RiskEngine(
        max_symbol_notional_minor=500_000_000,
        max_portfolio_notional_minor=1_000_000_000,
        max_daily_loss_minor=10_000_000,
        max_open_orders=10,
        concentration_limit_pct=1.0,
    )
    return RiskStage(
        engine,
        client=client,  # type: ignore[arg-type]
        limits=PreTradeLimits(
            max_order_qty=limits.get("max_order_qty", 100.0),
            max_order_notional_minor=limits.get("max_order_notional_minor", 100_000_000),
            price_band_pct=0.02,
        ),
        kill_switch=KillSwitch(),
        throttle=OrderRateThrottle(10, 14, clock=lambda: 0.0),
        strategy="test",
        clock=SimulatedClock(1_700_000_000_000_000_000),
    )


def _signal(signal: Signal = Signal.BUY, price: float = 2000.129) -> TradeSignal:
    return TradeSignal(symbol=SYMBOL, signal=signal, confidence=0.9, price=price)


async def test_crypto_buy_is_sized_in_fractional_steps_and_ticks() -> None:
    stage = _stage(CryptoFakeClient())
    intent = await stage.process(_signal())
    assert intent is not None
    assert intent.limit_price == 2000.12  # BUY rounds down onto the 0.01 tick
    assert 0 < intent.qty < 100
    assert intent.qty != int(intent.qty)  # fractional, not whole lots
    assert round(intent.qty / 0.0001) == pytest.approx(intent.qty / 0.0001)


async def test_crypto_notional_cap_binds_the_quantity() -> None:
    stage = _stage(CryptoFakeClient(), max_order_notional_minor=100_000)  # $1,000
    intent = await stage.process(_signal(price=2000.0))
    assert intent is not None
    assert intent.qty * intent.limit_price <= 1000.0 + 1e-6
    assert intent.qty == pytest.approx(0.5)


async def test_crypto_rejects_without_broker_tick() -> None:
    client = CryptoFakeClient()
    client.tick = None
    stage = _stage(client)
    assert await stage.process(_signal()) is None
    assert stage.reject_reasons == {"no_tick_size": 1}


async def test_crypto_rejects_without_qty_step() -> None:
    client = CryptoFakeClient()
    client.qty_step = None
    stage = _stage(client)
    assert await stage.process(_signal()) is None
    assert stage.reject_reasons == {"no_qty_step": 1}


async def test_crypto_quantity_below_min_is_rejected() -> None:
    stage = _stage(CryptoFakeClient(), max_order_qty=0.00005)
    assert await stage.process(_signal()) is None
    assert stage.reject_reasons == {"size_zero": 1}


async def test_crypto_sell_is_capped_at_sellable_and_never_shorts() -> None:
    client = CryptoFakeClient()
    stage = _stage(client)
    assert await stage.process(_signal(Signal.SELL)) is None  # nothing held: no short
    assert stage.reject_reasons == {"size_zero": 1}
    client.can_sell = 0.37655
    intent = await stage.process(_signal(Signal.SELL, price=2000.0))
    assert intent is not None
    assert intent.qty <= 0.37655


def test_crypto_market_requires_crypto_block() -> None:
    with pytest.raises(ValidationError, match="requires a trading.crypto block"):
        _trading(crypto=None)
    with pytest.raises(ValidationError):  # step and minimum have no defaults
        CryptoSettings.model_validate({})


async def test_crypto_queries_pin_currency_and_refuse_a_mismatch(
    fake_crypto_sdk: type[FakeCryptoTradeContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    import pandas as pd
    from futu import RET_OK

    seen: dict[str, object] = {}

    def _accinfo(self: FakeCryptoTradeContext, **kwargs: object) -> tuple[int, pd.DataFrame]:
        seen.update(kwargs)
        return RET_OK, pd.DataFrame({"total_assets": [1.0], "cash": [1.0], "currency": ["HKD"]})

    monkeypatch.setattr(fake_crypto_sdk, "accinfo_query", _accinfo)
    async with _live_client(_settings(security_firm="FUTUINC")) as client:
        with pytest.raises(RuntimeError, match="expected USD"):
            await client.get_portfolio()
    assert seen["currency"] == "USD"


@pytest.mark.parametrize(
    ("amount", "minor"), [(0.5199, 52), (0.019, 2), (19.99, 1999), (2000.12, 200012), (0.0, 0)]
)
def test_minor_units_ceil_never_understates(amount: float, minor: int) -> None:
    assert to_minor_units_ceil(amount) == minor


async def test_low_priced_coin_notional_is_not_understated() -> None:
    client = CryptoFakeClient()
    stage = _stage(client, max_order_notional_minor=10_000)  # $100 cap
    intent = await stage.process(
        TradeSignal(symbol=SYMBOL, signal=Signal.BUY, confidence=0.9, price=2000.0)
    )
    assert intent is not None
    assert intent.qty * intent.limit_price <= 100.0 + 1e-9
