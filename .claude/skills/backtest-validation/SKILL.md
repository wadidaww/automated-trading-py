---
name: backtest-validation
description: How to produce and judge honest backtest / research results for this repo — shared live/backtest code path, fill and cost models (HK/US fees, lots, ticks, slippage), bias checks (look-ahead, survivorship, leakage), purged/walk-forward CV, deflated Sharpe, correct metric annualization. Load before building or changing src/trader/evaluation, model training, or before reporting any performance number.
---

# Backtest validation

## Rules
1. **The same code runs in backtest and in live.**
   - The backtester replays recorded `QuoteEvent`s (or L2/ticker events) through the factory's
     `DataStage` → `SignalStage` → `RiskStage`.
   - Only the execution stage (a simulated fill model) and the clock are swapped.
   - Strategy code never reads wall-clock time.
2. **No fabricated numbers.** No data means no metrics. The CLI must fail loudly.
3. **Mark-to-market equity.** Equity is marked at a fixed frequency (e.g. 1m or daily),
   compounding, starting from a stated capital. Metrics come from that series. They do not come
   from a per-trade P&L list.
4. **Annualise with the actual frequency.**
   - Daily: √252.
   - Minute bars in HK: √(252·330), because there are about 330 minutes per session.
   - Subtract the risk-free rate at the same frequency.
   - Report sample size, and refuse to compute Sharpe below about 30 observations.

## Fill model
- Marketable orders fill at the opposite touch plus modelled slippage. Never fill at the last
  price or the signal-bar close.
- Limit orders fill only when the price trades *through* the limit. Optionally, model queue
  position ahead (L2 depth at the level).
- Add latency: decision → OpenD → exchange (configurable, e.g. 20–100ms). Use the book as of
  arrival time.
- Round qty to lot size and price to the tick ladder. Reject what the live system would reject.
- Model partial fills and auction sessions if the strategy trades in them.

## Costs (HK, approximate — verify against current Futu and HKEX schedules)
| Item | Rate |
|---|---|
| Stamp duty | 0.1% of value, both sides, rounded up to HKD 1 |
| SFC levy | 0.0027% |
| AFRC levy | 0.00015% |
| HKEX trading fee | 0.00565% |
| Settlement fee | about 0.002% (min/max apply, verify) |
| Futu commission | about 0.03% (with a minimum) |
| Futu platform fee | fixed per order or tiered |

US costs:
- per-share commission and platform fee
- SEC fee (sells)
- FINRA TAF (sells)

Put these in a `CostModel` shared by backtest and TCA.

## Bias checks (each should be a test or an assertion)
- **Look-ahead:** features at t use only data ≤ t. Test: perturbing data at t+1 must not change
  the decision at t.
- **Labels:** forward returns or triple-barrier with horizon h. Purge training samples whose
  label window overlaps the test fold, and embargo ≥ h.
- **Survivorship:** point-in-time universe; include delisted names.
- **Corporate actions:** adjusted prices for signals, raw prices for fills and lots.
- **Train/serve skew:** a single feature module. Assert that the backtest features equal the
  live `DataStage` features on the same input.

## Validation protocol
- Walk-forward, or combinatorial purged k-fold CV (López de Prado).
- Log every trial: params, data hash, code SHA, metrics.
- Report the **Deflated Sharpe Ratio** and **PBO** alongside the raw Sharpe, plus:
  - turnover
  - bps per trade vs. cost per trade
  - hit rate and payoff
  - max drawdown and Calmar
  - capacity (% ADV)
- A "confidence" used for sizing must be calibrated (reliability curve / Brier score) before it
  feeds Kelly.

## Output template
```
Strategy / commit / data range / universe / capital
Gross vs net Sharpe (freq, n obs) | DSR | PBO | MaxDD | turnover | bps/trade | cost/trade
Known limitations (what the fill model does not capture)
```
