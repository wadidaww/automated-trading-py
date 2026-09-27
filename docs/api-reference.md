# API Reference

## `FutuClient` (`src/trader/api/client.py`)

Async client for Futu OpenD quote/trade operations.

```python
FutuClient(
    host="127.0.0.1",
    port=11111,
    max_retries=5,
    heartbeat_interval_s=10,
    rate_limit_requests=300,
    rate_limit_window_s=30,
    trade_market=TrdMarket.HK,
    trd_env=TrdEnv.SIMULATE,
    acc_id=None,
    allow_paper_fallback=True,
    connection_timeout_s=0.2,
)
```

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `connect()` | `None` | Connect with bounded retries and jitter |
| `close()` | `None` | Close API contexts and stop heartbeat |
| `get_quote(symbol)` | `QuoteResponse` | Get current quote for a symbol |
| `get_stock_info(symbol)` | `StockInfoResponse` | Stock snapshot with valuation metrics |
| `place_order(symbol, qty, side, ...)` | `OrderResponse` | Place BUY/SELL order |
| `list_orders(symbol?)` | `list[OrderStatusResponse]` | List account orders |
| `get_order_status(order_id)` | `OrderStatusResponse` | Get one order status |
| `get_positions()` | `list[PositionResponse]` | Get current positions |
| `get_portfolio()` | `PortfolioResponse` | Get account portfolio |
| `get_portfolio_condition()` | `PortfolioConditionResponse` | Combined account + positions |

### Response types

- `QuoteResponse` — symbol, price
- `StockInfoResponse` — symbol, name, price, pe_ratio, pb_ratio, lot_size, listing_date
- `OrderResponse` — order_id, status, symbol, order_side, qty, price
- `OrderStatusResponse` — order_id, status, symbol, order_side, qty, dealt_qty, price, avg_fill_price
- `PositionResponse` — symbol, quantity, can_sell_qty, avg_cost, market_value, nominal_price, unrealized_pnl
- `PortfolioResponse` — account_id, total_assets, market_value, cash, available_cash, unrealized_pnl, realized_pnl

## `TradingPipeline` (`src/trader/pipeline/pipeline.py`)

Event-driven async pipeline orchestrator.

```python
TradingPipeline(factory=None, config=None, client=None, queue_maxsize=1000)
```

### Methods

| Method | Description |
|--------|-------------|
| `start()` | Start background worker task |
| `stop()` | Cancel worker and await completion |
| `drain()` | Wait until in-flight queue is drained |
| `submit(quote)` | Submit a QuoteEvent into the pipeline |

### Stage types

Stages chain via typed dataclasses; each stage implements `IStage[InputT, OutputT]`:

| Type | Module | Fields |
|------|--------|--------|
| `QuoteEvent` | `trader.api.quote_handler` | symbol, price, timestamp |
| `FeatureWindow` | `trader.pipeline.data_stage` | symbol, price, z_score, momentum, trend_strength, volatility, rsi, bb_position, price_acceleration |
| `TradeSignal` | `trader.pipeline.signal_stage` | symbol, signal, confidence, price |
| `ApprovedOrder` | `trader.pipeline.risk_stage` | symbol, qty, side, price |
| `OrderReceipt` | `trader.pipeline.execution_stage` | order_id, status |

## `DataNormalizer` (`src/trader/data/normalizer.py`)

Stateless feature engineering. Static method `add_features(df)` adds: RSI-14, MACD, Bollinger Bands, VWAP, OBV, log return, realized volatility, z-score, and lag features.

## `RiskEngine` (`src/trader/risk/risk_engine.py`)

Hard-gate risk checks evaluated under 1ms. Inputs via `RiskInput` dataclass, outputs `RiskDecision(approved, reason)`.

Gates run in order — first failure wins: `symbol_notional_limit`, `portfolio_notional_limit`, `daily_loss_limit`, `open_orders_limit`, `zero_portfolio_value`, `concentration_limit`, `latency_budget_exceeded`.

## `KellyCriterion` (`src/trader/risk/kelly_criterion.py`)

Fractional Kelly position sizing. `calculate(win_rate, win_loss_ratio)` returns the allocation fraction.

## `ISignalModel` (`src/trader/model/base.py`)

Abstract interface for signal models. Implement `predict(features)`, `fit(X, y)`, `save(path)`, `load(path)`.

Implementations: `MeanReversionModel`, `EnsembleSignalModel`, `GradientBoostingModel`, `TransformerPriceModel`.

Selected via `model.type` in config: `mean_reversion` (default), `ensemble`, `gradient_boosting` (falls back to `mean_reversion` if scikit-learn is unavailable).

## `OrderManager` (`src/trader/execution/order_manager.py`)

In-memory idempotent order management with state machine (PENDING → SUBMITTED → FILLED/CANCELLED).

## `TradingService` (`src/trader/execution/trading_service.py`)

Higher-level trading workflow: stock inspection, target evaluation, buy/sell order coordination.

## Configuration

`AppConfig` (`src/trader/utils/config.py`) — Pydantic `BaseSettings` model with nested sections: `OpendConfig`, `TradingSettings`, `ModelSettings`, `PipelineSettings`, `LoggingSettings`, `MetricsSettings`.
