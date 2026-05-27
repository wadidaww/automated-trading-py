from __future__ import annotations

from futu_trader.risk.kelly_criterion import KellyCriterion
from futu_trader.risk.risk_engine import RiskEngine, RiskInput


def test_risk_engine_rejects_symbol_limit() -> None:
    engine = RiskEngine(
        max_symbol_notional_minor=100,
        max_portfolio_notional_minor=1000,
        max_daily_loss_minor=1000,
        max_open_orders=10,
        concentration_limit_pct=0.5,
    )
    decision = engine.evaluate(RiskInput("700.HK", 2, 60, 0, 0, 0, 0, 1000))
    assert not decision.approved


def test_kelly_fraction() -> None:
    assert KellyCriterion(0.25).calculate(0.6, 2.0) > 0.0
