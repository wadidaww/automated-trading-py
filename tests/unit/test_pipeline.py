from __future__ import annotations

from helpers import run_pipeline


async def test_pipeline_processes_event() -> None:
    pipeline = await run_pipeline("HK.00700", 100.0)
    assert [e["status"] for e in pipeline.audit_stage.events] == ["WORKING"]
