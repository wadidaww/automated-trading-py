"""Backtest fill simulator."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(slots=True)
class SimulatedFill:
    """Simulated fill result."""

    quantity: int
    price_minor: int


class FillSimulator:
    """Apply slippage/spread to synthetic fills."""

    def __init__(self, slippage_bps: int = 5, spread_bps: int = 2) -> None:
        self.slippage_bps = slippage_bps
        self.spread_bps = spread_bps

    def simulate(self, quantity: int, mid_price_minor: int) -> SimulatedFill:
        """Return simulated partial/full fill."""
        slippage = (mid_price_minor * self.slippage_bps) // 10000
        spread = (mid_price_minor * self.spread_bps) // 10000
        random_part = random.randint(0, max(1, quantity // 10))
        filled = max(1, quantity - random_part)
        return SimulatedFill(quantity=filled, price_minor=mid_price_minor + slippage + spread)
