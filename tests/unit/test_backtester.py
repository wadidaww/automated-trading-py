from __future__ import annotations

from trader.evaluation.backtester import Backtester


def test_backtester_returns_metrics() -> None:
    result = Backtester().run([])
    assert "sharpe" in result
    assert "max_drawdown" in result
