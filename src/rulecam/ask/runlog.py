"""Ask's run log: one JSON line per answer, hash-chained, under `<data>/ask/runs.jsonl`.

Each row holds the question, the answer and its numbers, every step with its timing, model and
result (as Spark saw it, within the result budget), Spark's time per turn, the models used, the
SHA-256 of the instructions the models got (`prompt_sha256`) and the deployed commit
(`git_sha`). Rows are chained like the evidence log: `sha256 = SHA-256(prev_sha256 + the row's
canonical JSON without sha256)`, starting from 64 zeros, so a row edited or removed later breaks
every hash after it. The row's `sha256` is the answer's `receipt`.

The evidence chain itself is not used: its record kinds are fixed by the contracts, and none of
them is an Ask answer.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path
from typing import Final

from rulecam.ask.results import fit_result
from rulecam.ask.trace import AskAnswer
from rulecam.contracts import Clock
from rulecam.log import get_logger

_log = get_logger(__name__)

GENESIS: Final = "0" * 64
GIT_TIMEOUT_S: Final = 2.0


def git_sha(configured: str = "") -> str:
    """The deployed commit: `RULECAM_GIT_SHA` if set, else `git rev-parse HEAD`, else empty."""
    if configured:
        return configured
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git from PATH
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def canonical(row: dict[str, object]) -> str:
    """A row's canonical JSON: sorted keys, no spaces, UTF-8 text."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def row_hash(prev: str, row: dict[str, object]) -> str:
    """`SHA-256(prev + canonical(row without sha256))`, hex."""
    body = {k: v for k, v in row.items() if k != "sha256"}
    return hashlib.sha256((prev + canonical(body)).encode("utf-8")).hexdigest()


class AskRunLog:
    """Appends one hash-chained JSON line per Ask answer."""

    def __init__(self, path: Path, *, clock: Clock, git_sha: str, max_result_chars: int) -> None:
        """Create the log.

        Args:
            path: The JSON-lines file (created on the first answer).
            clock: Time source for each row's `ts`.
            git_sha: The deployed commit (`git_sha()`).
            max_result_chars: Characters of each step's result kept (as Spark saw it).
        """
        self._path = path
        self._clock = clock
        self._git_sha = git_sha
        self._max_chars = max_result_chars
        self._lock = threading.Lock()
        self._prev: str | None = None  # the newest row's hash, read from the file once

    @property
    def path(self) -> Path:
        """The log file."""
        return self._path

    def append(self, answer: AskAnswer) -> str:
        """Write the answer's row and return its hash (the answer's receipt).

        Raises:
            OSError: When the file cannot be written.
        """
        with self._lock:
            prev = self._prev if self._prev is not None else self._last_hash()
            row = {**self._row(answer), "prev_sha256": prev}
            row["sha256"] = row_hash(prev, row)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as out:
                out.write(canonical(row) + "\n")
            self._prev = str(row["sha256"])
        _log.info("ask_run_logged", sha256=row["sha256"], seconds=answer.seconds)
        return str(row["sha256"])

    def _row(self, answer: AskAnswer) -> dict[str, object]:
        steps = [
            {
                "turn": step.turn,
                "action": step.action.model_dump(mode="json", exclude_defaults=True),
                "seconds": step.seconds,
                "model": step.model,
                "frames": step.frames,
                "result": json.loads(fit_result(step.result, self._max_chars)),
            }
            for step in answer.steps
        ]
        return {
            "ts": self._clock.now().isoformat(),
            "conversation_id": answer.conversation_id,
            "question": answer.question,
            "answer": answer.answer,
            "numbers": answer.numbers,
            "seconds": answer.seconds,
            "models": answer.models,
            "turns": [t.model_dump(mode="json") for t in answer.turns],
            "steps": steps,
            "prompt_sha256": answer.prompt_sha256,
            "git_sha": self._git_sha,
        }

    def _last_hash(self) -> str:
        """The newest row's hash (64 zeros for a new log)."""
        if not self._path.is_file():
            return GENESIS
        last = ""
        with self._path.open("rb") as rows:
            for line in rows:
                if line.strip():
                    last = line.decode("utf-8")
        if not last:
            return GENESIS
        return str(json.loads(last)["sha256"])


def verify_run_log(path: Path) -> int:
    """Check every row's hash and link; return how many rows there are.

    Raises:
        ValueError: At the first row whose hash or link does not match.
    """
    prev, count = GENESIS, 0
    for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("prev_sha256") != prev or row.get("sha256") != row_hash(prev, row):
            msg = f"run log row {number} does not match its hash chain"
            raise ValueError(msg)
        prev, count = str(row["sha256"]), count + 1
    return count
