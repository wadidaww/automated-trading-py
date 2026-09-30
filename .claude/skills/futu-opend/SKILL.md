---
name: futu-opend
description: Correct usage of Futu OpenD / futu-api in this repo — symbol format, quote subscriptions and push handlers, thread→asyncio handoff, order placement and Futu order statuses, portfolio/position queries, historical klines, rate limits, reconnection, and SIMULATE vs REAL safety. Load before touching src/trader/api, src/trader/execution, or anything that calls OpenD.
---

# Futu OpenD integration guide

The reference is https://openapi.futunn.com/futu-api-doc/. Limits and enums change between SDK
versions, so check the docs for anything this page marks *verify*. `futu-api` is **not installed**
in the dev/CI containers. Code against `trader.api.client.FutuClient` and test with
`tests/helpers/fakes.py`.

## Symbols
Futu codes are `MARKET.CODE`: `HK.00700`, `US.AAPL`, `SH.600519`, `SZ.000001`. `700.HK` is
**wrong** (current configs use it — fix when touched). Normalize once at config load.

## Market data: push, don't poll
```python
from futu import OpenQuoteContext, SubType, StockQuoteHandlerBase, RET_OK

class QuotePush(StockQuoteHandlerBase):
    def __init__(self, loop, sink):
        super().__init__(); self._loop, self._sink = loop, sink
    def on_recv_rsp(self, rsp_pb):
        ret, df = super().on_recv_rsp(rsp_pb)          # runs on the SDK thread
        if ret == RET_OK:
            for row in df.itertuples(index=False):        # keep this minimal
                self._loop.call_soon_threadsafe(self._sink, row.code, row.last_price, row.data_date, row.data_time)
        return ret, df

ctx.set_handler(QuotePush(loop, book.on_quote))
ctx.subscribe(codes, [SubType.QUOTE, SubType.ORDER_BOOK, SubType.TICKER], subscribe_push=True)
```
- **Never** `await` or touch asyncio objects directly from a handler. Only use
  `loop.call_soon_threadsafe`.
- The sink should *conflate* (latest value per symbol) rather than block on a bounded queue.
- Subscription quota is limited per account tier, and a subscription must be held ≥1 minute
  before `unsubscribe` (*verify*).
- Use exchange timestamps (`data_date` + `data_time`, HKT), not `datetime.now()`.
- Handlers you will need:
  - `OrderBookHandlerBase` (L2)
  - `TickerHandlerBase` (trades)
  - `CurKlineHandlerBase`
  - `SysNotifyHandlerBase` (connection events → reconnect + re-subscribe)

## Trading
```python
from futu import OpenSecTradeContext, TrdEnv, TrdMarket, TrdSide, OrderType, SecurityFirm
trd = OpenSecTradeContext(filter_trdmarket=TrdMarket.HK, host=h, port=p, security_firm=SecurityFirm.FUTUSECURITIES)
ret, accs = trd.get_acc_list()          # assert configured acc_id exists with expected trd_env
if env == TrdEnv.REAL:
    ret, _ = trd.unlock_trade(password_md5=secret)   # from secret store, never logged
ret, df = trd.place_order(price=px, qty=qty, code="HK.00700", trd_side=TrdSide.BUY,
                          order_type=OrderType.NORMAL, trd_env=env, acc_id=acc_id,
                          remark=client_order_id)     # dedupe/reconcile key (length-limited, verify)
order_id = df["order_id"][0]            # store Futu's id — not a local uuid
```
- **Never pass `acc_id=0`** (or leave it empty) in REAL. It means "first account".
- `TradeOrderHandlerBase` / `TradeDealHandlerBase` push order updates and fills, and they drive
  the order state machine. Poll `order_list_query` only for reconciliation.
- Futu `OrderStatus` values that must be handled:
  - Working: `WAITING_SUBMIT`, `SUBMITTING`, `SUBMITTED`, `FILLED_PART`
  - Terminal: `FILLED_ALL`, `CANCELLED_PART`, `CANCELLED_ALL`, `FAILED`, `DISABLED`, `DELETED`,
    `SUBMIT_FAILED`
  - Transitional: `CANCELLING_PART`, `CANCELLING_ALL`
- HK orders must be whole **board lots** (`lot_size` from `get_market_snapshot` /
  `get_stock_basicinfo`) and on the **HK tick ladder** (0.001 … 5.0 depending on price band).

## Portfolio
- `accinfo_query(trd_env, acc_id, currency=...)`: cash, total_assets, market_val, power.
- `position_list_query(...)`: qty, can_sell_qty, cost_price, market_val, pl_val.
- `order_list_query` / `deal_list_query`: the day's orders and fills.

Keep an in-memory portfolio book updated from pushes. Resync on a timer, off the hot path.
Never make these calls per signal.

## Historical data
`request_history_kline(code, start, end, ktype=KLType.K_1M, autype=AuType.QFQ, max_count=1000, page_req_key=key)`
Loop while `page_req_key` is not None. It consumes the **history kline quota** (unique symbols
per 30 days, *verify*). Store the result as Parquet under `data/raw/`.

## Rate limits (per account or connection, *verify* before relying on them)
- `place_order`: about 15 per 30s, with a minimum interval between orders.
- `modify_order`: about 20 per 30s.
- `order_list_query`, `position_list_query`, `accinfo_query`: about 10 per 30s.
- `get_market_snapshot`: about 60 per 30s, up to 400 codes per call.

Use **one token bucket per API** and give order placement priority. Do not busy-poll.

## Blocking and threads
Context constructors connect synchronously. Every SDK call blocks. Run them in a dedicated
executor, never on the event loop.

## Environment safety
- `--mode paper` → `TrdEnv.SIMULATE`.
- `--mode live` → `TrdEnv.REAL`. Requires the prod config, an explicit opt-in, and **no
  simulator fallback**. If OpenD is unavailable, exit non-zero.
- The health check must report real connection and unlock state.
