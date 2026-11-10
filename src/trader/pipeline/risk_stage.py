"""Risk stage: the pipeline adapter around :class:`~trader.risk.pre_trade.PreTradeRisk`.

The stage maps a model signal to an order side, hands it to the pre-trade risk service and keeps
reject counters for the pipeline. All sizing, rounding and limit logic lives in
``trader.risk.pre_trade``; every path fails closed there.
"""

from __future__ import annotations

from typing import Final

from trader.core.clock import Clock
from trader.core.orders import OrderIntent, Side
from trader.model.base import Signal
from trader.pipeline.base import IStage
from trader.pipeline.signal_stage import TradeSignal
from trader.ports.broker import BrokerClient
from trader.risk.kelly_criterion import KellyCriterion
from trader.risk.kill_switch import KillSwitch
from trader.risk.pre_trade import (
    PreTradeLimits,
    PreTradeRequest,
    PreTradeRisk,
    Rejection,
    VolTargetSizing,
)
from trader.risk.risk_engine import RiskEngine
from trader.risk.throttle import OrderRateThrottle
from trader.utils.logger import get_logger

__all__ = ["PreTradeLimits", "RiskStage"]

logger = get_logger("risk_stage")

# HOLD (and anything added to Signal later) is deliberately absent: it never becomes an order.
_ORDER_SIDES: Final[dict[Signal, Side]] = {Signal.BUY: "BUY", Signal.SELL: "SELL"}


class RiskStage(IStage[TradeSignal, OrderIntent | None]):
    """Pre-trade risk gate for pipeline signals.

    Checks the kill switch, drops non-actionable signals (HOLD), then delegates to
    ``PreTradeRisk``. The first failing check decides the reject reason.
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
        vol_target: VolTargetSizing | None = None,
    ) -> None:
        self.risk = PreTradeRisk(
            engine=engine,
            client=client,
            limits=limits,
            kill_switch=kill_switch,
            throttle=throttle,
            kelly=kelly,
            strategy=strategy,
            clock=clock,
            vol_target=vol_target,
        )
        self.engine = engine
        self.kill_switch = kill_switch
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
        result = await self.risk.check(
            PreTradeRequest(
                symbol=item.symbol,
                side=side,
                confidence=item.confidence,
                price=item.price,
                target_exposure=item.target_exposure,
                realised_vol=item.realised_vol,
            )
        )
        if isinstance(result, Rejection):
            return self._reject(item, result.reason)
        return result

    def _reject(self, item: TradeSignal, reason: str) -> OrderIntent | None:
        """Count and log a rejected signal. Always returns None (the rejection)."""
        self.signals_dropped += 1
        self.reject_reasons[reason] = self.reject_reasons.get(reason, 0) + 1
        logger.info("signal_rejected", symbol=item.symbol, reason=reason, signal=item.signal.value)
        return None
