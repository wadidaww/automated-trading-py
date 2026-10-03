"""Risk stage: turns a trade signal into a sized, rounded, limit-checked order intent.

Every path fails closed. Missing portfolio, position, order or lot-size data rejects the signal;
there are no default sizes and no default portfolio values.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Final

from trader.api.broker import BrokerClient
from trader.api.client import find_position_by_symbol
from trader.api.models import StockInfoResponse
from trader.core.clock import Clock, WallClock
from trader.core.market_rules import (
    round_down_to_lot,
    round_down_to_step,
    round_to_tick,
    rules_for,
)
from trader.core.orders import OrderIntent, Side, from_futu_status, make_client_order_id
from trader.model.base import Signal
from trader.pipeline.base import IStage
from trader.pipeline.signal_stage import TradeSignal
from trader.risk.kelly_criterion import KellyCriterion
from trader.risk.kill_switch import KillSwitch
from trader.risk.risk_engine import RiskEngine, RiskInput
from trader.risk.throttle import OrderRateThrottle
from trader.utils.logger import get_logger
from trader.utils.maths import to_minor_units, to_minor_units_ceil

logger = get_logger("risk_stage")

CONFIDENCE_SIZE_TIERS: tuple[tuple[float, float], ...] = (
    (0.85, 1.0),
    (0.70, 0.6),
)
DEFAULT_SIZE_MULTIPLIER = 0.3

# HOLD (and anything added to Signal later) is deliberately absent: it never becomes an order.
_ORDER_SIDES: Final[dict[Signal, Side]] = {Signal.BUY: "BUY", Signal.SELL: "SELL"}


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


@dataclass(slots=True)
class PortfolioState:
    """Portfolio snapshot consumed by risk evaluation. Notionals include working BUY orders."""

    portfolio_value_minor: int
    symbol_notional_minor: int
    current_positions_notional_minor: int
    daily_pnl_minor: int
    open_orders: int
    can_sell_qty: float


class RiskStage(IStage[TradeSignal, OrderIntent | None]):
    """Pre-trade risk gate.

    Order of checks: kill switch → actionable side → live state → lot size → tick rounding and
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
        self.signals_dropped = 0
        self.reject_reasons: dict[str, int] = {}

    async def process(self, item: TradeSignal) -> OrderIntent | None:
        """Evaluate a signal against every pre-trade check.

        Returns:
            OrderIntent when every check passes, None when any rejects.
        """
        if self.kill_switch.is_tripped():
            return self._reject(item, "kill_switch")
        side = _ORDER_SIDES.get(item.signal)
        if side is None:
            return self._reject(item, "not_actionable")
        if item.price <= 0:
            return self._reject(item, "invalid_price")
        try:
            state = await self._get_portfolio_state(item.symbol)
            info = await self._client.get_stock_info(item.symbol)
        except Exception:
            logger.warning("risk_state_unavailable", symbol=item.symbol, exc_info=True)
            return self._reject(item, "state_unavailable")
        instrument = self._instrument(item.symbol, info)
        if isinstance(instrument, str):
            return self._reject(item, instrument)
        if info.price <= 0:
            return self._reject(item, "no_reference_price")
        return self._evaluate(item, side, state, instrument, info.price)

    @staticmethod
    def _instrument(symbol: str, info: StockInfoResponse) -> Instrument | str:
        """Quantity and tick rules for ``symbol``, or the reject reason when the data is missing.

        Equities need a positive board lot. Crypto has no lots: it needs a quantity step and a
        broker-supplied tick, and rejects rather than guessing either.
        """
        if rules_for(symbol).fractional_qty:
            if not info.qty_step or info.qty_step <= 0:
                return "no_qty_step"
            if not info.tick_size or info.tick_size <= 0:
                return "no_tick_size"
            return Instrument(
                step=info.qty_step, min_qty=info.min_qty or info.qty_step, tick=info.tick_size
            )
        if not info.lot_size or info.lot_size <= 0:
            return "no_lot_size"
        return Instrument(step=float(info.lot_size), min_qty=float(info.lot_size), tick=None)

    def _evaluate(
        self,
        item: TradeSignal,
        side: Side,
        state: PortfolioState,
        instrument: Instrument,
        reference_price: float,
    ) -> OrderIntent | None:
        """Price, size and gate one actionable signal against a live snapshot."""
        limit_price = round_to_tick(item.symbol, item.price, side, instrument.tick)
        if limit_price <= 0:
            return self._reject(item, "invalid_price")
        # Fat-finger guard: the limit must sit near the broker's own last price, not just near
        # the price the signal was computed from.
        if abs(limit_price - reference_price) / reference_price > self._limits.price_band_pct:
            return self._reject(item, "price_band")

        qty = self._size(item, side, state, limit_price, instrument)
        if qty < instrument.min_qty or qty <= 0:
            return self._reject(item, "size_zero")

        reduces_position = side == "SELL" and not self._limits.allow_short
        decision = self.engine.evaluate(
            RiskInput(
                symbol=item.symbol,
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
        if decision.reason == "daily_loss_limit":
            self.kill_switch.trip("daily_loss_limit")
        if not decision.approved:
            return self._reject(item, decision.reason)
        if self.kill_switch.is_tripped():
            return self._reject(item, "kill_switch")
        if not self._throttle.try_acquire():
            return self._reject(item, "order_rate_limit")

        self._seq += 1
        return OrderIntent(
            client_order_id=make_client_order_id(self._strategy, item.symbol, side, self._seq),
            symbol=item.symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            strategy=self._strategy,
            created_ns=self._clock.now_ns(),
            reference_price=reference_price,
        )

    def _size(
        self,
        item: TradeSignal,
        side: Side,
        state: PortfolioState,
        limit_price: float,
        instrument: Instrument,
    ) -> float:
        """Quantity after Kelly sizing and every per-order cap, on the instrument's lot/step.

        0 means do not trade. Equities size in whole lots; crypto in decimal steps.
        """
        price_minor = to_minor_units_ceil(limit_price)
        if price_minor <= 0 or item.confidence <= 0 or state.portfolio_value_minor <= 0:
            return 0
        kelly_fraction = self._kelly_fraction(item.confidence)
        if kelly_fraction <= 0:
            return 0
        budget = int(
            state.portfolio_value_minor * kelly_fraction * self._size_multiplier(item.confidence)
        )
        fractional = rules_for(item.symbol).fractional_qty
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

    def _reject(self, item: TradeSignal, reason: str) -> OrderIntent | None:
        """Count and log a rejected signal. Always returns None (the rejection)."""
        self.signals_dropped += 1
        self.reject_reasons[reason] = self.reject_reasons.get(reason, 0) + 1
        logger.info("signal_rejected", symbol=item.symbol, reason=reason, signal=item.signal.value)
        return None

    async def _get_portfolio_state(self, symbol: str) -> PortfolioState:
        """Fetch live portfolio, positions and working orders from the broker.

        Raises:
            Exception: Any fetch failure propagates; the caller rejects the signal.
        """
        portfolio = await self._client.get_portfolio()
        positions = await self._client.get_positions()
        orders = await self._client.list_orders()

        working = [o for o in orders if from_futu_status(o.status).is_working]
        working_buy_minor: defaultdict[str, int] = defaultdict(int)
        for o in working:
            if o.order_side == "BUY" and o.price is not None:
                remaining = max(o.qty - o.dealt_qty, 0)
                working_buy_minor[o.symbol] += math.ceil(remaining * to_minor_units_ceil(o.price))

        symbol_pos = find_position_by_symbol(positions, symbol)
        daily_pnl = (portfolio.realized_pnl or 0.0) + (portfolio.unrealized_pnl or 0.0)
        return PortfolioState(
            portfolio_value_minor=to_minor_units(portfolio.total_assets),
            symbol_notional_minor=(
                (to_minor_units(symbol_pos.market_value) if symbol_pos else 0)
                + working_buy_minor.get(symbol, 0)
            ),
            current_positions_notional_minor=(
                sum(to_minor_units(pos.market_value) for pos in positions)
                + sum(working_buy_minor.values())
            ),
            daily_pnl_minor=to_minor_units(daily_pnl),
            open_orders=len(working),
            can_sell_qty=symbol_pos.can_sell_qty if symbol_pos else 0,
        )

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
