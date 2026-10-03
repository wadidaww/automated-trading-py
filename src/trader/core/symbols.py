"""Futu security code normalisation.

Futu codes are ``MARKET.CODE`` (``HK.00700``, ``US.AAPL``, ``SH.600519``, ``CC.BTCUSD``).
Configs and humans often write ``700.HK`` or ``AAPL.US``; normalise once at config load and
use the Futu form everywhere else.
"""

from __future__ import annotations

from typing import Final

CRYPTO_MARKET: Final[str] = "CC"
MARKETS: Final[frozenset[str]] = frozenset(
    {"HK", "US", "SH", "SZ", "SG", "JP", "AU", CRYPTO_MARKET}
)
_HK_CODE_WIDTH: Final[int] = 5
# Futu crypto trading pairs are base+quote tickers (``BTCUSD``); ``CC.BTC`` is an index.
_CRYPTO_MIN_PAIR_LEN: Final[int] = 6


def normalize_symbol(raw: str) -> str:
    """Convert a security code to Futu ``MARKET.CODE`` form.

    Accepts ``HK.00700``, ``HK.700``, ``700.HK``, ``0700.hk``, ``AAPL.US`` and ``us.aapl``.

    Args:
        raw: Security code in any supported spelling.

    Returns:
        str: Canonical Futu code, e.g. ``HK.00700``.

    Raises:
        ValueError: When the code has no recognised market prefix or suffix.
    """
    text = raw.strip().upper()
    head, sep, tail = text.partition(".")
    if not sep or not head or not tail:
        raise ValueError(f"symbol must be MARKET.CODE or CODE.MARKET: {raw!r}")
    if head in MARKETS:
        market, code = head, tail
    elif tail in MARKETS:
        market, code = tail, head
    else:
        raise ValueError(f"unknown market in symbol: {raw!r}")
    if market == "HK":
        if not code.isdigit():
            raise ValueError(f"HK codes are numeric: {raw!r}")
        code = code.lstrip("0").zfill(_HK_CODE_WIDTH)
    elif market == CRYPTO_MARKET and not (code.isalnum() and len(code) >= _CRYPTO_MIN_PAIR_LEN):
        raise ValueError(f"CC codes must be trading pairs such as CC.BTCUSD: {raw!r}")
    return f"{market}.{code}"


def market_of(symbol: str) -> str:
    """Return the market prefix of a normalised Futu code (``HK.00700`` → ``HK``)."""
    return symbol.partition(".")[0]
