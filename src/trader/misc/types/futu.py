"""Shared type aliases."""

from __future__ import annotations

from typing import Literal

# Keep side values strict while allowing direct use of TrdSide.BUY / TrdSide.SELL.
type TradeSide = Literal["BUY", "SELL"]
