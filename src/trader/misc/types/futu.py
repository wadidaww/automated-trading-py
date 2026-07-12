"""Shared type aliases."""

from __future__ import annotations

from typing import Literal, TypeAlias

# Keep side values strict while allowing direct use of TrdSide.BUY / TrdSide.SELL.
TradeSide: TypeAlias = Literal["BUY", "SELL"]
