"""The Claude Code PreToolUse guard blocks live trading and secrets, and nothing else."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "guard_live_trading.py"


def _run(tool_name: str, tool_input: dict[str, object]) -> int:
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script path
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, check=False
    )
    return result.returncode


@pytest.mark.parametrize(
    "command",
    [
        "PYTHONPATH=src python -m trader --mode live --config config/config.prod.yaml",
        "python -m trader --mode=live",
        "docker compose run trader --mode live",
        "export TRADER_LIVE_CONFIRM=1",
        "TRADER_LIVE_CONFIRM=1 make run",
        "python -c 'ctx.unlock_trade(password_md5=x)'",
        "cat .env",
        "cp .env.example .env",
    ],
)
def test_blocks_live_trading_and_secrets(command: str) -> None:
    assert _run("Bash", {"command": command}) == 2


@pytest.mark.parametrize(
    "command",
    [
        "PYTHONPATH=src python -m trader --mode paper --duration 60",
        "grep -rn 'TRADER_LIVE_CONFIRM' src/",
        'grep -rn -- "--mode live" README.md',
        "grep -rn unlock_trade src/trader/api",
        "cat .env.example",
        "PYTHONPATH=src pytest tests/unit",
    ],
)
def test_allows_ordinary_commands(command: str) -> None:
    assert _run("Bash", {"command": command}) == 0


def test_blocks_real_trd_env_in_config_edits() -> None:
    edit = {"file_path": "/repo/config/config.prod.yaml", "new_string": 'trd_env: "REAL"'}
    assert _run("Edit", edit) == 2
    write = {"file_path": "config/x.yaml", "content": "trading:\n  trd_env: REAL\n"}
    assert _run("Write", write) == 2


def test_allows_simulate_configs_and_real_outside_config() -> None:
    edit = {"file_path": "/repo/config/config.dev.yaml", "new_string": 'trd_env: "SIMULATE"'}
    assert _run("Edit", edit) == 0
    test_edit = {"file_path": "/repo/tests/unit/test_x.py", "new_string": 'trd_env: "REAL"'}
    assert _run("Edit", test_edit) == 0


def test_blocks_writing_dotenv_and_tolerates_bad_payloads() -> None:
    assert _run("Write", {"file_path": "/repo/.env", "content": "FUTU_ACCOUNT_ID=1"}) == 2
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script path
        [sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
