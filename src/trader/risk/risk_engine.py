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
    # A SELL that only closes an existing long lowers exposure, so notional and
    # concentration gates do not apply to it. Loss and open-order gates still do.
    reduces_position: bool = False


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
        """Run all risk checks under a 1 ms budget.

        Checks run in order; the first failing gate decides the rejection reason.
        """
        started = perf_counter()
        if -inp.daily_pnl_minor > self.max_daily_loss_minor:
            return RiskDecision(False, "daily_loss_limit")
        if inp.open_orders >= self.max_open_orders:
            return RiskDecision(False, "open_orders_limit")
        if inp.portfolio_value_minor <= 0:
            return RiskDecision(False, "zero_portfolio_value")
        if not inp.reduces_position:
            proposed_notional = inp.quantity * inp.price_minor
            symbol_notional = inp.current_symbol_notional_minor + proposed_notional
            if symbol_notional > self.max_symbol_notional_minor:
                return RiskDecision(False, "symbol_notional_limit")
            portfolio_notional = inp.current_portfolio_notional_minor + proposed_notional
            if portfolio_notional > self.max_portfolio_notional_minor:
                return RiskDecision(False, "portfolio_notional_limit")
            if symbol_notional / inp.portfolio_value_minor > self.concentration_limit_pct:
                return RiskDecision(False, "concentration_limit")
        if (perf_counter() - started) * 1000 >= 1.0:
            return RiskDecision(False, "latency_budget_exceeded")
        return RiskDecision(True, "approved")
