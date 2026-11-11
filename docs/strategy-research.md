# Strategy search on real prices (November 2026)

Research record behind `model.type: sma_trend`, `config/config.crypto.sma.yaml` and
`config/config.hk.nasdaq.sma.yaml`. The scripts, data and full output live in the project files
(`strategy-research/strategy-build/`: `GATE.md`, `lib.py`, `experiments.py`, `analyse.py`,
`out_design.txt`, `out_holdout.txt`, `out_analysis.txt`); they are not part of this repository.

## Assumptions

- Futu cash account: long only, no margin, no shorting, no leveraged products.
- Account size US$10,000 for fixed fees (checked at US$3,000 and US$50,000).
- Signal on the close of day t, fill at the close of day t+1. Idle cash earns nothing.
- Costs per side: crypto 20 bps; US 2 bps plus US$2 an order; HK ETFs 9.1 bps plus about US$2.3
  an order; HK stocks add 10 bps stamp duty. Futu's crypto fee and coin list could not be
  confirmed from its documentation, so 20 bps and BTC/ETH only are assumptions.
- Data: Coinbase daily and 6-hour candles; Yahoo daily bars (dividend adjusted). Yahoo's HK ETF
  history has unit glitches that were repaired or cut; 3010.HK was unusable and dropped.

## Gate (written before the holdout was looked at)

Design period to 2021 (crypto) or 2019 (stocks); holdout from there to 10 November 2026, run once.
A rule passes only if, on both periods and after costs, it (1) beats Treasury bills, (2) beats
buy-and-hold of the same assets scaled down to the same worst loss, (3) still beats bills with
costs doubled, one more day of delay, and lookbacks at x0.75 and x1.25, and (4) is still positive
with its best calendar year removed.

## Result: 44 variants, 10 passed, 2 are built

Holdout figures, a year, after costs. "Hold" is buy-and-hold of the same asset.

| Rule | Return | Worst loss | Hold return | Hold worst loss | Verdict |
|---|---|---|---|---|---|
| BTC above 200-day average | 17.6% | -36% | 9.8% | -67% | pass, built |
| QQQ above 200-day average | 15.8% | -25% | 19.0% | -35% | pass, built for HK.02834 |
| QQQ trend x 15% vol target | 11.6% | -11% | 19.0% | -35% | pass, config only (`trend`) |
| SPY above 200-day average | 10.8% | -24% | 14.7% | -34% | pass, not built (US session) |
| US ETF rotation, top 3 of 10 | 12.3% | -13% | 9.6% | -23% | pass, needs a ranking job |
| BTC/ETH relative-strength switch | 12.5% | -40% | 0.2% | -68% | pass, needs a cross-symbol feature |
| 52-week-high breakout, US large caps | 16.1% | -19% | 17.0% | -35% | pass, biased universe |
| 52-week-high breakout, HK large caps | 16.0% | -22% | 9.2% | -35% | pass, biased universe |
| 12-1 momentum, HK large caps | 7.6% | -24% | 9.2% | -35% | pass, biased, fee sensitive |
| Coin basket trend x vol | 8.7% | -28% | -4.1% | -71% | pass, biased coin list |

Failed: every ETH-only rule, the PR #14 BTC trend x vol rule (14.3% a year on the holdout but
negative without 2023), BTC core-plus-sleeve, 6-hour breakouts with a trailing stop, RSI(2) dip
buying on BTC, ETH, SPY, QQQ, 2800 and 2834, every Hang Seng (2800) timing rule, the ETF trend
books, HK ETF rotation (passed the holdout, failed the design years), US large-cap 12-1 momentum,
and overnight-only index holding (fees take everything at this account size).

## What this does and does not show

- No equity rule beat simply holding the index on raw return. The timing rules gave most of the
  return for roughly two thirds of the worst loss. On BTC the 200-day rule beat holding outright
  over 2022 to 2026 because it sat out most of 2022.
- Statistical proof is weak. The deflated Sharpe ratio over the 44 trials is at most 0.26, far
  under the usual 0.95 bar, and every block-bootstrap Sharpe interval reaches zero or close to it.
  Five to seven years of holdout is too short to prove an edge of this size.
- "Biased universe" rows use today's large companies only (free data has no delisted names), so
  their returns are overstated. They are compared with the same names held equally.
- 2834 has no design period of its own (listed 2016); the evidence for it is QQQ's.
- Every number on this page comes from research data that is not in this repository (Coinbase
  and Yahoo bars fetched on 10 and 11 November 2026). Nothing in CI recomputes them.
- The daily job reads Futu forward-adjusted bars, not Yahoo or Coinbase bars. A 200-day comparison
  on slightly different closes can cross on a different day.
- Fills are generous. The tests fill the whole order at the next close. The job rests a limit at
  the previous close inside a 2% band, one order a session, so an exit on a gap day can be late.
  Real worst losses will be deeper than the table.

## Reproducing the built rules with the in-repo backtester

With a daily bar CSV (a date column and `close`) for each symbol:

```bash
PYTHONPATH=src python -m trader.research.daily_backtest --config config/config.crypto.sma.yaml \
    --bars <BTC daily closes>.csv --symbol-weight 1.0 --cost-bps 20 --start 2022-01-01
PYTHONPATH=src python -m trader.research.daily_backtest --config config/config.hk.nasdaq.sma.yaml \
    --bars <2834 daily closes>.csv --symbol-weight 1.0 --cost-bps 9.1 --fee 2.3 --start 2020-01-01
```

On the research bars this gave BTC 17.5% a year with a 36% worst loss (holding: 9.1%, 67%) and
2834 16.5% with 21% (holding: 18.8%, 35%), matching the research scripts. It runs the live
feature, model and sizing code, so the agreement checks the implementation, not the edge.

## Not done

Post-earnings drift (no earnings data), board-lot rounding, a clean point-in-time stock universe,
US session support in the daily job, and a ranking job for rotation and breakout rules.
