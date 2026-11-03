"""Fakes for rule extensions: a Muse Code process that writes files, and the reference check.

The reference check is stored as `.txt`: it is model-style output (data), not project code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rulecam.clients.meta_hosted.muse_code import ProcessResult

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "extensions" / "pallet_above_line"
EXAMPLE_CHECK = (EXAMPLE_DIR / "check.txt").read_text(encoding="utf-8")
EXAMPLE_TESTS = (EXAMPLE_DIR / "test_check.txt").read_text(encoding="utf-8")


@dataclass
class FakeMuseCode:
    """A `ProcessRunner` that plays Muse Code: writes `files` into the workspace and exits."""

    files: Mapping[str, str] = field(
        default_factory=lambda: {"check.py": EXAMPLE_CHECK, "test_check.py": EXAMPLE_TESTS}
    )
    stdout: str = '{"type": "message", "text": "wrote check.py and test_check.py"}\nnot json\n'
    returncode: int = 0
    calls: list[tuple[list[str], dict[str, str], set[str]]] = field(default_factory=list)

    def __call__(
        self, argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float
    ) -> ProcessResult:
        seen = {p.name for p in cwd.iterdir()}
        self.calls.append((list(argv), dict(env), seen))
        for name, text in self.files.items():
            (cwd / name).write_text(text, encoding="utf-8")
        return ProcessResult(self.returncode, self.stdout, "boom" if self.returncode else "")
