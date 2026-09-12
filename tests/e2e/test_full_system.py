from __future__ import annotations

from helpers import run_pipeline


async def test_full_system_paper_flow() -> None:
    pipeline = await run_pipeline("3690.HK", 200.0)
    assert len(pipeline.audit_stage.events) == 1
