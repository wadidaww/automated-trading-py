"""Position and PnL tracker."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Position:
    """Position stats for one symbol."""

    quantity: int = 0
    avg_cost_minor: int = 0
    realized_pnl_minor: int = 0


class PositionTracker:
    """Track average cost and realized PnL in integer minor units."""

    def __init__(self) -> None:
        self.positions: dict[str, Position] = {}

    def apply_fill(self, symbol: str, qty: int, price_minor: int) -> None:
        """Apply fill update to internal position."""
        position = self.positions.setdefault(symbol, Position())
        new_qty = position.quantity + qty
        if new_qty == 0:
            position.quantity = 0
            position.avg_cost_minor = 0
            return
        total_cost = position.avg_cost_minor * position.quantity + price_minor * qty
        position.quantity = new_qty
        position.avg_cost_minor = total_cost // new_qty

    def net_delta(self, symbol: str) -> int:
        """Return net shares for symbol."""
        return self.positions.get(symbol, Position()).quantity
