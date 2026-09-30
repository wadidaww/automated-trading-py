"""Core domain types shared by live trading, backtesting and research.

Nothing in this package may import futu, pandas or pydantic: it sits on the hot path and is
reused by the backtester, so it must stay dependency-free and allocation-light.
"""
