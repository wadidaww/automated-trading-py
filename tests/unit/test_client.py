from __future__ import annotations

import pytest

from futu_trader.api.client import FutuClient


@pytest.mark.asyncio
async def test_client_get_quote() -> None:
    async with FutuClient() as client:
        quote = await client.get_quote("700.HK")
    assert quote.symbol == "700.HK"
