"""Risk stage."""

from __future__ import annotations

from dataclasses import dataclass

from futu_trader.pipeline.signal_stage import TradeSignal
from futu_trader.risk.risk_engine import RiskEngine, RiskInput


@dataclass(slots=True)
class ApprovedOrder:
    """Order approved by risk."""

    symbol: str
    qty: int
    side: str


class RiskStage:
    """Risk gate stage."""

    def __init__(self, engine: RiskEngine) -> None:
        self.engine = engine
        self.signals_dropped = 0

    async def process(self, item: TradeSignal) -> ApprovedOrder | None:
        """Evaluate signal against limits."""
        decision = self.engine.evaluate(
            RiskInput(
                symbol=item.symbol,
                quantity=1,
                price_minor=int(item.price * 100),
                current_symbol_notional_minor=0,
                current_portfolio_notional_minor=0,
                daily_pnl_minor=0,
                open_orders=0,
                portfolio_value_minor=1_000_000,
            )
        )
        if not decision.approved:
            self.signals_dropped += 1
            return None
        side = "BUY" if item.signal.value == "BUY" else "SELL"
        return ApprovedOrder(symbol=item.symbol, qty=1, side=side)
