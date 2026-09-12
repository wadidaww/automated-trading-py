from __future__ import annotations

from helpers import run_pipeline


async def test_pipeline_end_to_end() -> None:
    pipeline = await run_pipeline("9988.HK", 88.0)
    assert pipeline.audit_stage.events
