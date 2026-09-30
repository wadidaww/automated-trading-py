from __future__ import annotations

import pytest

from trader.evaluation.backtester import Backtester
from trader.evaluation.metrics import Trade


def test_backtester_returns_metrics() -> None:
    result = Backtester().run([Trade(1_000, 100_000), Trade(-500, 100_000), Trade(2_000, 100_000)])
    assert set(result) == {"sharpe", "max_drawdown"}
    assert result["max_drawdown"] > 0


def test_backtester_refuses_to_fabricate_metrics() -> None:
    with pytest.raises(ValueError, match="no trades"):
        Backtester().run([])
