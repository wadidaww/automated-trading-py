"""Pattern tools from the command line.

    PYTHONPATH=src python -m trader.patterns train --symbols HK.00700,HK.09988
    PYTHONPATH=src python -m trader.patterns scan --symbol HK.00700
    PYTHONPATH=src python -m trader.patterns stats --symbols HK.00700,HK.09988 --horizon 10

``--synthetic`` swaps cached history for synthetic bars, to try the tools without OpenD.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from trader.data.fetcher import DEFAULT_CACHE_DIR, load_history
from trader.patterns.candlesticks import detect_candlestick_patterns
from trader.patterns.chart import detect_chart_patterns, patterns_frame
from trader.patterns.recognizer import PatternRecognizer, TrainSettings, weak_labels
from trader.patterns.stats import pattern_outcomes
from trader.research.synthetic import synthetic_bars

DEFAULT_MODEL = "data/models/pattern_recognizer.pt"


def _bars(
    symbols: list[str], ktype: str, cache_dir: str, synthetic: bool
) -> dict[str, pd.DataFrame]:
    if synthetic:
        return {s: synthetic_bars(n_bars=1500, seed=i) for i, s in enumerate(symbols)}
    return {s: load_history(s, ktype, cache_dir) for s in symbols}


def _train(args: argparse.Namespace) -> None:
    extra = []
    symbols = [s for s in args.symbols.split(",") if s] if args.symbols else []
    for bars in _bars(symbols, args.ktype, args.cache_dir, args.synthetic).values():
        extra += weak_labels(bars, detect_chart_patterns(bars))
    model = PatternRecognizer()
    metrics = model.fit(
        TrainSettings(n_per_class=args.per_class, epochs=args.epochs, seed=args.seed), extra=extra
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    model.save(args.out)
    metrics["real_windows"] = len(extra)
    metrics["model"] = args.out
    print(json.dumps(metrics, indent=2))


def _scan(args: argparse.Namespace) -> None:
    bars = _bars([args.symbol], args.ktype, args.cache_dir, args.synthetic)[args.symbol]
    recent_start = max(len(bars) - args.last, 0)
    pd.set_option("display.width", 200)
    candles = detect_candlestick_patterns(bars).iloc[-args.candle_bars :]
    print(f"Candlestick patterns, last {args.candle_bars} bars:")
    for when, row in candles.iterrows():
        hits = {k: int(v) for k, v in row.items() if v != 0}
        if hits:
            print(f"  {when}: {hits}")
    rules = [p for p in detect_chart_patterns(bars) if p.detected_at >= recent_start]
    print(f"\nRule-based chart patterns detected in the last {args.last} bars:")
    frame = patterns_frame(rules, bars.index)
    if frame.empty:
        print("  none")
    else:
        cols = [
            "name",
            "direction",
            "start_time",
            "detected_at_time",
            "breakout",
            "target",
            "confidence",
        ]
        print(frame[cols].to_string(index=False))
    model_path = Path(args.model)
    print("\nNeural network chart patterns:")
    if not model_path.exists():
        print(f"  no model at {model_path}; run `python -m trader.patterns train` first")
        return
    model = PatternRecognizer.load(str(model_path))
    found = model.scan(bars.iloc[recent_start:], threshold=args.threshold)
    if found.empty:
        print("  none above the threshold")
    else:
        print(found.drop(columns=["start", "end"]).to_string(index=False))


def _stats(args: argparse.Namespace) -> None:
    symbols = [s for s in args.symbols.split(",") if s]
    table = pattern_outcomes(
        _bars(symbols, args.ktype, args.cache_dir, args.synthetic), args.horizon
    )
    pd.set_option("display.width", 200)
    print(table.to_string(index=False))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(args.out, index=False)


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and run a pattern command."""
    parser = argparse.ArgumentParser(
        prog="python -m trader.patterns", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ktype", default="K_DAY")
    common.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    common.add_argument("--synthetic", action="store_true", help="use synthetic bars")

    train = sub.add_parser("train", parents=[common], help="train the neural pattern recognizer")
    train.add_argument(
        "--symbols", default="", help="comma-separated; adds rule-labelled real windows"
    )
    train.add_argument("--out", default=DEFAULT_MODEL)
    train.add_argument("--per-class", type=int, default=600)
    train.add_argument("--epochs", type=int, default=15)
    train.add_argument("--seed", type=int, default=0)
    train.set_defaults(run=_train)

    scan = sub.add_parser("scan", parents=[common], help="find patterns in one symbol's history")
    scan.add_argument("--symbol", required=True)
    scan.add_argument("--model", default=DEFAULT_MODEL)
    scan.add_argument("--last", type=int, default=250, help="bars to search for chart patterns")
    scan.add_argument("--candle-bars", type=int, default=5, help="bars to list candlesticks for")
    scan.add_argument("--threshold", type=float, default=0.6)
    scan.set_defaults(run=_scan)

    stats = sub.add_parser("stats", parents=[common], help="forward returns after each pattern")
    stats.add_argument("--symbols", required=True)
    stats.add_argument("--horizon", type=int, default=10)
    stats.add_argument("--out", default="")
    stats.set_defaults(run=_stats)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    args.run(args)


if __name__ == "__main__":
    main()
