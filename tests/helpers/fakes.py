"""Fake Futu OpenD contexts used to stub the vendor SDK in tests."""

from __future__ import annotations

from futu import RET_OK
from pandas import DataFrame


class FakeQuoteContext:
    """Stand-in for :class:`futu.OpenQuoteContext`."""

    def __init__(self, **_: object) -> None:
        self.closed = False

    def get_global_state(self) -> tuple[int, str]:
        return RET_OK, "ok"

    def get_market_snapshot(self, _: list[str]) -> tuple[int, DataFrame]:
        return RET_OK, DataFrame(
            {
                "code": ["700.HK"],
                "name": ["Tencent"],
                "last_price": [100.5],
                "pe_ratio": [12.3],
                "pb_ratio": [1.8],
                "lot_size": [100],
                "list_time": ["2004-06-16"],
            }
        )

    def close(self) -> None:
        self.closed = True


class FakeTradeContext:
    """Stand-in for :class:`futu.OpenSecTradeContext`."""

    def __init__(self, **_: object) -> None:
        self.closed = False

    def place_order(self, *_: object, **__: object) -> tuple[int, DataFrame]:
        return RET_OK, DataFrame({"order_id": ["123"], "order_status": ["SUBMITTED"]})

    def order_list_query(self, **_: object) -> tuple[int, DataFrame]:
        return RET_OK, DataFrame(
            {
                "order_id": ["123"],
                "code": ["700.HK"],
                "order_status": ["FILLED"],
                "trd_side": ["BUY"],
                "qty": [10],
                "dealt_qty": [10],
                "price": [100.0],
                "dealt_avg_price": [99.5],
            }
        )

    def position_list_query(self, **_: object) -> tuple[int, DataFrame]:
        return RET_OK, DataFrame(
            {
                "code": ["700.HK"],
                "qty": [10],
                "can_sell_qty": [8],
                "cost_price": [95.0],
                "market_val": [1000.0],
                "nominal_price": [100.0],
                "pl_val": [50.0],
            }
        )

    def accinfo_query(self, **_: object) -> tuple[int, DataFrame]:
        return RET_OK, DataFrame(
            {
                "total_assets": [1500.0],
                "market_val": [1000.0],
                "cash": [500.0],
                "avl_withdrawal_cash": [450.0],
                "unrealized_pl": [50.0],
                "realized_pl": [25.0],
            }
        )

    def close(self) -> None:
        self.closed = True
