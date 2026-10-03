from __future__ import annotations

import pytest

from trader.data import indicators


def test_short_windows_return_neutral_values() -> None:
    assert indicators.momentum([1.0, 2.0]) == 0.0
    assert indicators.trend_strength([1.0] * 9) == 0.0
    assert indicators.volatility([1.0] * 9) == 0.0
    assert indicators.rsi([1.0] * 14) == 50.0
    assert indicators.bollinger_position([1.0] * 19, 1.0, 0.1) == 0.5
    assert indicators.acceleration([1.0], 2.0) == 0.0


def test_momentum_is_fractional_change_and_guards_zero_base() -> None:
    assert indicators.momentum([100, 0, 0, 0, 110]) == pytest.approx(0.1)
    assert indicators.momentum([0, 1, 1, 1, 1]) == 0.0


def test_rsi_saturates_on_one_way_moves() -> None:
    assert indicators.rsi([float(i) for i in range(30)]) > 99
    assert indicators.rsi([float(-i) for i in range(30)]) == pytest.approx(0.0)


def test_trend_strength_sign_follows_direction() -> None:
    assert indicators.trend_strength([float(i + 1) for i in range(20)]) > 0
    assert indicators.trend_strength([float(20 - i) for i in range(20)]) < 0


def test_bollinger_position_midpoint_and_acceleration() -> None:
    assert indicators.bollinger_position([0.0] * 19 + [10.0], 10.0, 1.0) == pytest.approx(0.5)
    assert indicators.acceleration([1.0, 2.0], 5.0) == pytest.approx(2.0)
