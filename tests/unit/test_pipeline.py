from __future__ import annotations

from helpers import run_pipeline


async def test_pipeline_processes_event() -> None:
    pipeline = await run_pipeline("700.HK", 100.0)
    assert len(pipeline.audit_stage.events) >= 1
