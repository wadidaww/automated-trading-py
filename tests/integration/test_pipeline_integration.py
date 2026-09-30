from __future__ import annotations

from helpers import run_pipeline


async def test_pipeline_end_to_end() -> None:
    pipeline = await run_pipeline("HK.09988", 88.0)
    assert [e["status"] for e in pipeline.audit_stage.events] == ["WORKING"]
