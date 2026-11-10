"""Live portfolio snapshot for pre-trade risk, fetched from the broker port."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from trader.core.orders import from_futu_status
from trader.ports.broker import BrokerClient
from trader.ports.models import find_position_by_symbol
from trader.utils.maths import to_minor_units, to_minor_units_ceil


@dataclass(slots=True)
class PortfolioState:
    """Portfolio snapshot consumed by risk evaluation. Notionals include working BUY orders."""

    portfolio_value_minor: int
    symbol_notional_minor: int
    current_positions_notional_minor: int
    daily_pnl_minor: int
    open_orders: int
    can_sell_qty: float
    # For target-position sizing, which must know what is held and what is already on its way.
    symbol_qty: float = 0.0
    symbol_market_value_minor: int = 0
    symbol_working_orders: int = 0


async def load_portfolio_state(client: BrokerClient, symbol: str) -> PortfolioState:
    """Fetch live portfolio, positions and working orders from the broker.

    Working BUY orders count towards the symbol and portfolio notionals, so exposure that is not
    filled yet is never invisible to the limits.

    Raises:
        Exception: Any fetch failure propagates; the caller rejects the signal.
    """
    portfolio = await client.get_portfolio()
    positions = await client.get_positions()
    orders = await client.list_orders()

    working = [o for o in orders if from_futu_status(o.status).is_working]
    working_buy_minor: defaultdict[str, int] = defaultdict(int)
    for o in working:
        if o.order_side == "BUY" and o.price is not None:
            remaining = max(o.qty - o.dealt_qty, 0)
            working_buy_minor[o.symbol] += math.ceil(remaining * to_minor_units_ceil(o.price))

    symbol_pos = find_position_by_symbol(positions, symbol)
    daily_pnl = (portfolio.realized_pnl or 0.0) + (portfolio.unrealized_pnl or 0.0)
    return PortfolioState(
        portfolio_value_minor=to_minor_units(portfolio.total_assets),
        symbol_notional_minor=(
            (to_minor_units(symbol_pos.market_value) if symbol_pos else 0)
            + working_buy_minor.get(symbol, 0)
        ),
        current_positions_notional_minor=(
            sum(to_minor_units(pos.market_value) for pos in positions)
            + sum(working_buy_minor.values())
        ),
        daily_pnl_minor=to_minor_units(daily_pnl),
        open_orders=len(working),
        can_sell_qty=symbol_pos.can_sell_qty if symbol_pos else 0,
        symbol_qty=symbol_pos.quantity if symbol_pos else 0.0,
        symbol_market_value_minor=to_minor_units(symbol_pos.market_value) if symbol_pos else 0,
        symbol_working_orders=sum(1 for o in working if o.symbol == symbol),
    )
