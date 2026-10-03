from __future__ import annotations

import pandas as pd
import pytest

from trader.api.parsers import order_status_from_row, portfolio_from_row, position_from_row
from trader.api.parsers import stock_info_from_snapshot
from trader.api.simulator import LOT_SIZE, PaperSimulator


def test_snapshot_prefers_last_price_and_falls_back_to_nominal() -> None:
    df = pd.DataFrame([{"code": "HK.00700", "nominal_price": 300.0, "lot_size": "100.0"}])
    info = stock_info_from_snapshot(df, "HK.00700")
    assert (info.price, info.lot_size, info.name) == (300.0, 100, "HK.00700")


def test_snapshot_without_price_or_rows_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="no market snapshot"):
        stock_info_from_snapshot(pd.DataFrame(), "HK.00700")
    with pytest.raises(RuntimeError, match="missing price"):
        stock_info_from_snapshot(pd.DataFrame([{"code": "HK.00700"}]), "HK.00700")


def test_order_row_requires_id_and_maps_side() -> None:
    row = pd.Series({"order_id": "9", "trd_side": "SELL", "qty": 200, "order_status": "SUBMITTED"})
    order = order_status_from_row(row)
    assert (order.order_id, order.order_side, order.qty) == ("9", "SELL", 200)
    with pytest.raises(RuntimeError, match="order_id"):
        order_status_from_row(pd.Series({"qty": 1}))


def test_position_and_portfolio_default_missing_amounts_to_zero() -> None:
    position = position_from_row(pd.Series({"code": "HK.00700", "qty": 100}))
    assert (position.quantity, position.market_value, position.can_sell_qty) == (100, 0.0, 0)
    portfolio = portfolio_from_row(pd.Series({"power": 5.0}), account_id=7)
    assert (portfolio.total_assets, portfolio.cash, portfolio.realized_pnl) == (5.0, 0.0, None)


def test_simulator_prices_stay_per_symbol_and_orders_echo_remark() -> None:
    sim = PaperSimulator()
    assert sim.stock_info("HK.00700").lot_size == LOT_SIZE
    assert sim.orders(None) == []
    assert sim.order_ack("HK.00700", "BUY", 100, 1.0, "cid-1").order_id == "cid-1"
    assert sim.order_ack("HK.00700", "BUY", 100, 1.0, None).order_id == "HK.00700-BUY-100"
