"""The Claude Code PreToolUse hook that blocks `git push` while `make lint type` is red (4.4)."""

# ruff: noqa: S603 - our own script, fixed arguments

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "block-red-push.sh"
SETTINGS = ROOT / ".claude" / "settings.json"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

# A stand-in for `make`: records that it ran, exits with FAKE_MAKE_EXIT.
FAKE_MAKE = """#!/bin/sh
echo "$*" >> "$MAKE_LOG"
echo "mypy: error: something is red"
exit "${FAKE_MAKE_EXIT:-0}"
"""


def run_hook(tmp_path: Path, command: str, make_exit: int = 0) -> tuple[int, str, bool]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "make").write_text(FAKE_MAKE)
    (bin_dir / "make").chmod(0o755)
    log = tmp_path / "make.log"
    log.unlink(missing_ok=True)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    result = subprocess.run(
        [str(BASH), str(HOOK)],
        input=json.dumps(payload),
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "MAKE_LOG": str(log),
            "FAKE_MAKE_EXIT": str(make_exit),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.returncode, result.stderr, log.exists()


@pytest.mark.parametrize(
    "command",
    [
        "git push -u origin p1/ops-harness",
        "cd /repo && git push",
        "git -C /repo push origin HEAD",
        "git push",
    ],
)
def test_a_red_lint_blocks_the_push(tmp_path: Path, command: str) -> None:
    code, stderr, ran = run_hook(tmp_path, command, make_exit=2)
    assert ran
    assert code == 2
    assert "make lint type" in stderr
    assert "something is red" in stderr


def test_a_green_lint_lets_the_push_through(tmp_path: Path) -> None:
    code, _, ran = run_hook(tmp_path, "git push origin feature", make_exit=0)
    assert (code, ran) == (0, True)


@pytest.mark.parametrize(
    "command", ["git status", "gh pr create --base main", "echo pushed", "git pull", "ls push/"]
)
def test_other_commands_never_run_the_check(tmp_path: Path, command: str) -> None:
    code, _, ran = run_hook(tmp_path, command, make_exit=2)
    assert (code, ran) == (0, False)


def test_settings_wire_the_hook_to_bash() -> None:
    settings = json.loads(SETTINGS.read_text())
    (entry,) = settings["hooks"]["PreToolUse"]
    assert entry["matcher"] == "Bash"
    assert entry["hooks"][0]["command"].endswith("/.claude/hooks/block-red-push.sh")
