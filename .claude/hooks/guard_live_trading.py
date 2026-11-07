#!/usr/bin/env python3
"""PreToolUse guard: block agent actions that could trade with real money or expose secrets.

Claude Code passes the tool call as JSON on stdin. Exit code 2 blocks the call and shows stderr
to the model. Anything this script cannot parse is allowed, so a malformed payload never wedges
the session; the patterns are deliberately narrow so reading or grepping code is never blocked.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import PurePosixPath

_RUNS_TRADER = re.compile(r"-m\s+trader(\s|$)|docker\s+(compose\s+)?(run|exec|up)")
_LIVE_MODE = re.compile(r"--mode[=\s]+['\"]?live\b")
_LIVE_CONFIRM = re.compile(r"(^|[\s;&|(])(export\s+)?TRADER_LIVE_CONFIRM=")
_RUNS_PYTHON = re.compile(r"\bpython[0-9.]*\b")
_UNLOCK = re.compile(r"\bunlock_trade\s*\(")
_DOTENV = re.compile(r"(^|[\s/'\"<>])\.env(?![.\w])")
_REAL_ENV = re.compile(r"trd_env\s*:\s*['\"]?REAL\b")


def _bash_reason(command: str) -> str | None:
    if _LIVE_MODE.search(command) and _RUNS_TRADER.search(command):
        return "running the trader with --mode live"
    if _LIVE_CONFIRM.search(command):
        return "setting TRADER_LIVE_CONFIRM"
    if _UNLOCK.search(command) and _RUNS_PYTHON.search(command):
        return "calling unlock_trade"
    if _DOTENV.search(command):
        return "touching .env (it holds account secrets)"
    return None


def _edit_reason(tool_input: dict[str, object]) -> str | None:
    path = PurePosixPath(str(tool_input.get("file_path", "")))
    if path.name == ".env":
        return "writing .env (it holds account secrets)"
    if "config" not in path.parts:
        return None
    new_text = " ".join(
        str(tool_input.get(key, "")) for key in ("content", "new_string")
    ) + " ".join(str(edit.get("new_string", "")) for edit in _edits(tool_input))
    if _REAL_ENV.search(new_text):
        return "setting trd_env: REAL in a config file"
    return None


def _edits(tool_input: dict[str, object]) -> list[dict[str, object]]:
    edits = tool_input.get("edits")
    return [edit for edit in edits if isinstance(edit, dict)] if isinstance(edits, list) else []


def main() -> int:
    """Return 2 (block) with a reason on stderr, or 0 (allow)."""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if tool == "Bash":
        reason = _bash_reason(str(tool_input.get("command", "")))
    elif tool in {"Edit", "Write", "MultiEdit"}:
        reason = _edit_reason(tool_input)
    else:
        reason = None
    if reason is None:
        return 0
    print(
        f"Blocked by .claude/hooks/guard_live_trading.py: {reason}. Agents never trade live or "
        "handle account secrets in this repo. Ask the user to do this by hand if it is needed.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
