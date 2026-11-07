"""Pre-trade risk: turn an actionable signal into a sized, rounded, limit-checked order intent.

This is the risk service the pipeline's ``RiskStage`` wraps; anything else that places orders
(scheduled jobs, manual tools) can use it directly. Every path fails closed: missing portfolio,
position, order or lot-size data rejects; there are no default sizes and no default portfolio
values.
"""

from __future__ import annotations

from dataclasses import dataclass

from trader.core.clock import Clock, WallClock
from trader.core.market_rules import (
    round_down_to_lot,
    round_down_to_step,
    round_to_tick,
    rules_for,
)
from trader.core.orders import OrderIntent, Side, make_client_order_id
from trader.ports.broker import BrokerClient
from trader.ports.models import StockInfoResponse
from trader.risk.kelly_criterion import KellyCriterion
from trader.risk.kill_switch import KillSwitch
from trader.risk.portfolio_state import PortfolioState, load_portfolio_state
from trader.risk.risk_engine import RiskEngine, RiskInput
from trader.risk.throttle import OrderRateThrottle
from trader.utils.logger import get_logger
from trader.utils.maths import to_minor_units_ceil

logger = get_logger("pre_trade_risk")

CONFIDENCE_SIZE_TIERS: tuple[tuple[float, float], ...] = (
    (0.85, 1.0),
    (0.70, 0.6),
)
DEFAULT_SIZE_MULTIPLIER = 0.3


@dataclass(slots=True)
class PreTradeLimits:
    """Per-order limits applied before the portfolio-level risk engine."""

    max_order_qty: float
    max_order_notional_minor: int
    price_band_pct: float
    allow_short: bool = False


@dataclass(frozen=True, slots=True)
class Instrument:
    """Order-validity rules for one symbol: quantity step (lot), minimum and tick."""

    step: float
    min_qty: float
    tick: float | None  # None → the market's static tick ladder


@dataclass(frozen=True, slots=True)
class PreTradeRequest:
    """An actionable trade idea to be checked: what, which way, how sure and at what price."""

    symbol: str
    side: Side
    confidence: float
    price: float


@dataclass(frozen=True, slots=True)
class Rejection:
    """A request refused by a pre-trade check; ``reason`` names the first failing check."""

    reason: str


class PreTradeRisk:
    """Pre-trade risk gate.

    Order of checks: kill switch → valid price → live state → lot size → tick rounding and
    price band against a fresh broker snapshot → sizing (Kelly, lot, max qty/notional, sellable
    qty) → portfolio limits → kill switch again (it may have tripped during the broker calls) →
    order-rate throttle. The first failing check decides the reject reason. A daily-loss breach
    trips the (latched) kill switch.
    """

    def __init__(
        self,
        engine: RiskEngine,
        client: BrokerClient,
        limits: PreTradeLimits,
        kill_switch: KillSwitch,
        throttle: OrderRateThrottle,
        kelly: KellyCriterion | None = None,
        strategy: str = "default",
        clock: Clock | None = None,
    ) -> None:
        self.engine = engine
        self._client = client
        self._limits = limits
        self.kill_switch = kill_switch
        self._throttle = throttle
        self._kelly = kelly or KellyCriterion()
        self._strategy = strategy
        self._clock: Clock = clock or WallClock()
        # Seeded from wall time so ids stay unique across restarts until the store persists it.
        self._seq = self._clock.now_ns() // 1_000_000

    async def check(self, request: PreTradeRequest) -> OrderIntent | Rejection:
        """Run every pre-trade check on ``request``.

        Returns:
            OrderIntent when every check passes, otherwise the first Rejection.
        """
        if self.kill_switch.is_tripped():
            return Rejection("kill_switch")
        if request.price <= 0:
            return Rejection("invalid_price")
        try:
            state = await load_portfolio_state(self._client, request.symbol)
            info = await self._client.get_stock_info(request.symbol)
        except Exception:
            logger.warning("risk_state_unavailable", symbol=request.symbol, exc_info=True)
            return Rejection("state_unavailable")
        logger.info(
            "risk_inputs",
            symbol=request.symbol,
            signal=request.side,
            confidence=round(request.confidence, 4),
            signal_price=request.price,
            broker_price=info.price,
            lot_size=info.lot_size,
            qty_step=info.qty_step,
            tick_size=info.tick_size,
            min_qty=info.min_qty,
            portfolio_value_minor=state.portfolio_value_minor,
            symbol_notional_minor=state.symbol_notional_minor,
            positions_notional_minor=state.current_positions_notional_minor,
            daily_pnl_minor=state.daily_pnl_minor,
            open_orders=state.open_orders,
            can_sell_qty=state.can_sell_qty,
        )
        instrument = self._instrument(request.symbol, info)
        if isinstance(instrument, Rejection):
            return instrument
        if info.price <= 0:
            return Rejection("no_reference_price")
        return self._evaluate(request, request.side, state, instrument, info.price)

    @staticmethod
    def _instrument(symbol: str, info: StockInfoResponse) -> Instrument | Rejection:
        """Quantity and tick rules for ``symbol``, or the reject reason when the data is missing.

        Equities need a positive board lot. Crypto has no lots: it needs a quantity step and a
        broker-supplied tick, and rejects rather than guessing either.
        """
        if rules_for(symbol).fractional_qty:
            if not info.qty_step or info.qty_step <= 0:
                return Rejection("no_qty_step")
            if not info.tick_size or info.tick_size <= 0:
                return Rejection("no_tick_size")
            return Instrument(
                step=info.qty_step, min_qty=info.min_qty or info.qty_step, tick=info.tick_size
            )
        if not info.lot_size or info.lot_size <= 0:
            return Rejection("no_lot_size")
        return Instrument(step=float(info.lot_size), min_qty=float(info.lot_size), tick=None)

    def _evaluate(
        self,
        request: PreTradeRequest,
        side: Side,
        state: PortfolioState,
        instrument: Instrument,
        reference_price: float,
    ) -> OrderIntent | Rejection:
        """Price, size and gate one actionable signal against a live snapshot."""
        limit_price = round_to_tick(request.symbol, request.price, side, instrument.tick)
        if limit_price <= 0:
            return Rejection("invalid_price")
        # Fat-finger guard: the limit must sit near the broker's own last price, not just near
        # the price the signal was computed from.
        if abs(limit_price - reference_price) / reference_price > self._limits.price_band_pct:
            return Rejection("price_band")

        deviation = abs(limit_price - reference_price) / reference_price
        logger.info(
            "target_price",
            symbol=request.symbol,
            side=side,
            signal_price=request.price,
            limit_price=limit_price,
            reference_price=reference_price,
            deviation_pct=round(deviation * 100, 4),
            price_band_pct=round(self._limits.price_band_pct * 100, 4),
        )
        qty = self._size(request, side, state, limit_price, instrument)
        logger.info(
            "risk_sized",
            symbol=request.symbol,
            side=side,
            qty=qty,
            notional_minor=int(qty * to_minor_units_ceil(limit_price)),
            min_qty=instrument.min_qty,
            max_order_qty=self._limits.max_order_qty,
            max_order_notional_minor=self._limits.max_order_notional_minor,
        )
        if qty < instrument.min_qty or qty <= 0:
            return Rejection("size_zero")

        reduces_position = side == "SELL" and not self._limits.allow_short
        decision = self.engine.evaluate(
            RiskInput(
                symbol=request.symbol,
                quantity=qty,
                price_minor=to_minor_units_ceil(limit_price),
                current_symbol_notional_minor=state.symbol_notional_minor,
                current_portfolio_notional_minor=state.current_positions_notional_minor,
                daily_pnl_minor=state.daily_pnl_minor,
                open_orders=state.open_orders,
                portfolio_value_minor=state.portfolio_value_minor,
                reduces_position=reduces_position,
            )
        )
        logger.info(
            "risk_decision",
            symbol=request.symbol,
            approved=decision.approved,
            reason=decision.reason,
            qty=qty,
            limit_price=limit_price,
        )
        if decision.reason == "daily_loss_limit":
            self.kill_switch.trip("daily_loss_limit")
        if not decision.approved:
            return Rejection(decision.reason)
        if self.kill_switch.is_tripped():
            return Rejection("kill_switch")
        if not self._throttle.try_acquire():
            return Rejection("order_rate_limit")

        self._seq += 1
        logger.info(
            "order_intent_created",
            symbol=request.symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            reference_price=reference_price,
        )
        return OrderIntent(
            client_order_id=make_client_order_id(self._strategy, request.symbol, side, self._seq),
            symbol=request.symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            strategy=self._strategy,
            created_ns=self._clock.now_ns(),
            reference_price=reference_price,
        )

    def _size(
        self,
        request: PreTradeRequest,
        side: Side,
        state: PortfolioState,
        limit_price: float,
        instrument: Instrument,
    ) -> float:
        """Quantity after Kelly sizing and every per-order cap, on the instrument's lot/step.

        0 means do not trade. Equities size in whole lots; crypto in decimal steps.
        """
        price_minor = to_minor_units_ceil(limit_price)
        if price_minor <= 0 or request.confidence <= 0 or state.portfolio_value_minor <= 0:
            return 0
        kelly_fraction = self._kelly_fraction(request.confidence)
        if kelly_fraction <= 0:
            return 0
        multiplier = self._size_multiplier(request.confidence)
        budget = int(state.portfolio_value_minor * kelly_fraction * multiplier)
        logger.info(
            "kelly_sizing",
            symbol=request.symbol,
            confidence=round(request.confidence, 4),
            kelly_fraction=round(kelly_fraction, 4),
            size_multiplier=multiplier,
            budget_minor=budget,
        )
        fractional = rules_for(request.symbol).fractional_qty
        if fractional:
            qty = min(
                budget / price_minor,
                self._limits.max_order_qty,
                self._limits.max_order_notional_minor / price_minor,
            )
        else:
            qty = min(
                budget // price_minor,
                self._limits.max_order_qty,
                self._limits.max_order_notional_minor // price_minor,
            )
        if side == "SELL" and not self._limits.allow_short:
            qty = min(qty, state.can_sell_qty)
        if fractional:
            return round_down_to_step(qty, instrument.step)
        return float(round_down_to_lot(qty, int(instrument.step)))

    def _kelly_fraction(self, confidence: float) -> float:
        """Derive the Kelly fraction for a signal confidence value."""
        win_rate = min(confidence, 0.95)
        win_loss_ratio = 1.0 / max(1.0 - win_rate, 0.05)
        return self._kelly.calculate(win_rate, win_loss_ratio)

    @staticmethod
    def _size_multiplier(confidence: float) -> float:
        """Look up the confidence tier multiplier from CONFIDENCE_SIZE_TIERS."""
        return next(
            (mult for floor, mult in CONFIDENCE_SIZE_TIERS if confidence >= floor),
            DEFAULT_SIZE_MULTIPLIER,
        )
