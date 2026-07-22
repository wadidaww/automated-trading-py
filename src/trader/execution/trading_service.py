"""Higher-level trading workflow helpers."""

from __future__ import annotations

from dataclasses import dataclass

from trader.api.client import (
    FutuClient,
    OrderResponse,
    PortfolioConditionResponse,
    PositionResponse,
    StockInfoResponse,
)


@dataclass(slots=True)
class TargetEvaluation:
    """Target-hit evaluation for one symbol."""

    symbol: str
    current_price: float
    buy_target: float
    sell_target: float
    held_quantity: int
    buy_target_hit: bool
    sell_target_hit: bool


class TradingService:
    """Coordinate stock inspection, target checks, orders, and portfolio sync."""

    def __init__(self, client: FutuClient) -> None:
        self.client = client

    async def gather_stock_info(self, symbol: str) -> StockInfoResponse:
        """Fetch the stock snapshot used before trading."""
        return await self.client.get_stock_info(symbol)

    async def get_portfolio_condition(self) -> PortfolioConditionResponse:
        """Fetch the current account and positions."""
        return await self.client.get_portfolio_condition()

    async def evaluate_targets(
        self, symbol: str, buy_target: float, sell_target: float
    ) -> TargetEvaluation:
        """Check whether the market price has reached the configured targets."""
        stock_info, portfolio = await self._load_symbol_state(symbol)
        held_quantity = self._held_quantity(symbol, portfolio.positions)
        return TargetEvaluation(
            symbol=symbol,
            current_price=stock_info.price,
            buy_target=buy_target,
            sell_target=sell_target,
            held_quantity=held_quantity,
            buy_target_hit=stock_info.price <= buy_target,
            sell_target_hit=held_quantity > 0 and stock_info.price >= sell_target,
        )

    async def place_buy_order(
        self, symbol: str, qty: int, buy_target: float, order_type: str = "LIMIT"
    ) -> OrderResponse:
        """Place a buy order only after the buy target is reached."""
        evaluation = await self.evaluate_targets(symbol, buy_target, sell_target=float("inf"))
        if not evaluation.buy_target_hit:
            raise ValueError("buy target not reached")
        return await self.client.place_order(
            symbol=symbol,
            qty=qty,
            side="BUY",
            price=buy_target if order_type == "LIMIT" else None,
            order_type=order_type,
        )

    async def place_sell_order(
        self, symbol: str, qty: int, sell_target: float, order_type: str = "LIMIT"
    ) -> OrderResponse:
        """Place a sell order only after the sell target is reached and shares are held."""
        evaluation = await self.evaluate_targets(symbol, buy_target=0.0, sell_target=sell_target)
        if evaluation.held_quantity < qty:
            raise ValueError("insufficient position to sell")
        if not evaluation.sell_target_hit:
            raise ValueError("sell target not reached")
        return await self.client.place_order(
            symbol=symbol,
            qty=qty,
            side="SELL",
            price=sell_target if order_type == "LIMIT" else None,
            order_type=order_type,
        )

    async def get_order_status(self, order_id: str) -> str:
        """Fetch the latest order status."""
        return (await self.client.get_order_status(order_id)).status

    async def _load_symbol_state(
        self, symbol: str
    ) -> tuple[StockInfoResponse, PortfolioConditionResponse]:
        """Fetch quote and portfolio state together."""
        return await self.client.get_stock_info(symbol), await self.client.get_portfolio_condition()

    @staticmethod
    def _held_quantity(symbol: str, positions: list[PositionResponse]) -> int:
        """Get the held quantity for one symbol."""
        for position in positions:
            if position.symbol == symbol:
                return position.quantity
        return 0
