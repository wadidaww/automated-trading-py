"""Risk engine hard gates."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter


@dataclass(slots=True)
class RiskDecision:
    """Risk decision payload."""

    approved: bool
    reason: str


@dataclass(slots=True)
class RiskInput:
    """Risk input model using int minor units for money values."""

    symbol: str
    quantity: int
    price_minor: int
    current_symbol_notional_minor: int
    current_portfolio_notional_minor: int
    daily_pnl_minor: int
    open_orders: int
    portfolio_value_minor: int


class RiskEngine:
    """Hard-gate risk checks."""

    def __init__(
        self,
        max_symbol_notional_minor: int,
        max_portfolio_notional_minor: int,
        max_daily_loss_minor: int,
        max_open_orders: int,
        concentration_limit_pct: float,
    ) -> None:
        self.max_symbol_notional_minor = max_symbol_notional_minor
        self.max_portfolio_notional_minor = max_portfolio_notional_minor
        self.max_daily_loss_minor = max_daily_loss_minor
        self.max_open_orders = max_open_orders
        self.concentration_limit_pct = concentration_limit_pct

    def evaluate(self, inp: RiskInput) -> RiskDecision:
        """Run all risk checks under 1ms budget."""
        started = perf_counter()
        proposed_notional = inp.quantity * inp.price_minor
        if inp.current_symbol_notional_minor + proposed_notional > self.max_symbol_notional_minor:
            return RiskDecision(False, "symbol_notional_limit")
        if (
            inp.current_portfolio_notional_minor + proposed_notional
            > self.max_portfolio_notional_minor
        ):
            return RiskDecision(False, "portfolio_notional_limit")
        if -inp.daily_pnl_minor > self.max_daily_loss_minor:
            return RiskDecision(False, "daily_loss_limit")
        if inp.open_orders >= self.max_open_orders:
            return RiskDecision(False, "open_orders_limit")
        concentration = (inp.current_symbol_notional_minor + proposed_notional) / max(
            inp.portfolio_value_minor, 1
        )
        if concentration > self.concentration_limit_pct:
            return RiskDecision(False, "concentration_limit")
        elapsed_ms = (perf_counter() - started) * 1000
        if elapsed_ms >= 1.0:
            return RiskDecision(False, "latency_budget_exceeded")
        return RiskDecision(True, "approved")
