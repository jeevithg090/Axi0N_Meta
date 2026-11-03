"""Muse Code, Meta's coding agent, run headless: `muse exec --json` in a scratch workspace.

Muse Code is a terminal agent, not an HTTP model: its headless mode runs one prompt in the
current directory, edits files there and prints one JSON event per line, then exits. It
authenticates once per machine (`muse auth set --api-key-stdin`) and runs on Muse Spark 1.2 under
Model API pay-as-you-go pricing (Meta's "Build with Muse Code" post). This client:

1. writes the given files into a fresh temporary directory;
2. runs `<binary> exec --json [extra args] <prompt>` there, through an injectable process runner,
   with only `PATH`, `HOME`, `LANG` and `MUSE_*` variables in its environment (no Rulecam
   secrets), under a wall timeout;
3. counts the JSONL events, keeps the last message as a summary, and reads the requested output
   files back.

Every run goes through the replay cache (provider `meta-muse-code`), keyed by the files, the
prompt and the arguments: `record` stores the files Muse Code wrote, `replay` serves them with no
process and no network. The JSONL event schema is not published, so parsing is lenient: any JSON
object counts as an event, and `text`/`message`/`content` strings become the summary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rulecam.clients.errors import ClientConfigError, ModelResponseError
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

PROVIDER: Final = "meta-muse-code"
MUSE_CODE_MODEL: Final = "muse-spark-1.2"
DEFAULT_TIMEOUT_S: Final = 300.0
_SUMMARY_KEYS: Final = ("text", "message", "content")
_SUMMARY_CHARS: Final = 500
_STDERR_CHARS: Final = 400
_ENV_KEYS: Final = ("PATH", "HOME", "LANG")


@dataclass(frozen=True)
class ProcessResult:
    """What a finished process returned."""

    returncode: int
    stdout: str
    stderr: str


ProcessRunner = Callable[[Sequence[str], Path, Mapping[str, str], float], ProcessResult]


def run_process(
    argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float
) -> ProcessResult:
    """Run a command and capture its output (the default `ProcessRunner`).

    Raises:
        ModelResponseError: If it runs past `timeout_s`.
    """
    try:
        done = subprocess.run(  # noqa: S603 - argv is built by MuseCodeClient, no shell
            list(argv),
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"Muse Code ran longer than {timeout_s:g} s"
        raise ModelResponseError(msg) from exc
    return ProcessResult(done.returncode, done.stdout, done.stderr)


@dataclass(frozen=True)
class MuseCodeRun:
    """The files Muse Code left in the workspace, and what it said.

    Attributes:
        files: Each requested output file's text, by relative path.
        events: Number of JSON events it printed.
        summary: Its last message (trimmed), or "".
        model: The model Muse Code ran on.
    """

    files: dict[str, str]
    events: int
    summary: str
    model: str


class MuseCodeClient:
    """Runs Muse Code headless over a scratch workspace, through the replay cache."""

    def __init__(
        self,
        *,
        cache: ReplayCache,
        binary: str = "muse",
        extra_args: Sequence[str] = (),
        timeout_s: float = DEFAULT_TIMEOUT_S,
        model: str = MUSE_CODE_MODEL,
        runner: ProcessRunner = run_process,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        """Create the client.

        Args:
            cache: The replay cache every model call goes through.
            binary: The Muse Code executable (`RULECAM_MUSE_CODE_BIN`).
            extra_args: Arguments after `exec --json`, e.g. an approval mode for file edits.
            timeout_s: Wall timeout for one run.
            model: The model Muse Code is configured to run on (recorded, and part of the key).
            runner: Runs the process; tests pass a fake.
            which: Finds the executable on `PATH`.
        """
        self._cache = cache
        self._binary = binary
        self._args = tuple(extra_args)
        self._timeout_s = timeout_s
        self._model = model
        self._runner = runner
        self._which = which

    @property
    def model(self) -> str:
        """The model Muse Code runs on."""
        return self._model

    @property
    def available(self) -> bool:
        """Whether a run can be served: the CLI is installed, or the cache replays it."""
        return self._cache.mode == "replay" or self._which(self._binary) is not None

    def run(self, files: Mapping[str, str], prompt: str, outputs: Sequence[str]) -> MuseCodeRun:
        """Run one prompt over a workspace holding `files` and read `outputs` back.

        Args:
            files: Relative path → text, written before the run.
            prompt: The instruction for Muse Code.
            outputs: Relative paths to read back after the run.

        Returns:
            The outputs, the event count and the summary.

        Raises:
            ClientConfigError: If the CLI is not installed (and the cache does not replay it).
            ModelResponseError: If it fails, times out or leaves an output missing.
        """
        request: dict[str, object] = {
            "files": dict(files),
            "prompt": prompt,
            "args": list(self._args),
            "outputs": list(outputs),
        }
        entry = self._cache.call(
            PROVIDER, self._model, request, lambda: self._live(files, prompt, outputs)
        )
        reply = entry.response
        found = reply.get("files")
        if not isinstance(found, dict) or set(found) != set(outputs):
            msg = "Muse Code reply is missing output files"
            raise ModelResponseError(msg)
        return MuseCodeRun(
            files={str(k): str(v) for k, v in found.items()},
            events=int(str(reply.get("events", 0))),
            summary=str(reply.get("summary", "")),
            model=self._model,
        )

    def _live(
        self, files: Mapping[str, str], prompt: str, outputs: Sequence[str]
    ) -> dict[str, object]:
        path = self._which(self._binary)
        if path is None:
            msg = f"the Muse Code CLI ({self._binary!r}) is not installed on this machine"
            raise ClientConfigError(msg)
        with tempfile.TemporaryDirectory(prefix="rulecam-muse-") as tmp:
            workspace = Path(tmp)
            for name, text in files.items():
                target = workspace / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            argv = [path, "exec", "--json", *self._args, prompt]
            done = self._runner(argv, workspace, child_env(os.environ), self._timeout_s)
            if done.returncode != 0:
                msg = f"Muse Code exited with {done.returncode}: {done.stderr[-_STDERR_CHARS:]}"
                raise ModelResponseError(msg)
            events, summary = parse_events(done.stdout)
            _log.info("muse_code_ran", events=events, outputs=list(outputs))
            return {
                "files": {name: read_output(workspace, name) for name in outputs},
                "events": events,
                "summary": summary,
            }


def child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The environment Muse Code gets: `PATH`, `HOME`, `LANG` and `MUSE_*` only."""
    return {
        key: value for key, value in environ.items() if key in _ENV_KEYS or key.startswith("MUSE_")
    }


def parse_events(stdout: str) -> tuple[int, str]:
    """Count JSON events and keep the last message, ignoring lines that are not JSON objects."""
    count, summary = 0, ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        count += 1
        for key in _SUMMARY_KEYS:
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                summary = value.strip()[:_SUMMARY_CHARS]
    return count, summary


def read_output(workspace: Path, name: str) -> str:
    """Read one output file, refusing paths that leave the workspace.

    Raises:
        ModelResponseError: If the file is missing or outside the workspace.
    """
    target = (workspace / name).resolve()
    if not target.is_relative_to(workspace.resolve()) or not target.is_file():
        msg = f"Muse Code did not leave {name!r} in the workspace"
        raise ModelResponseError(msg)
    return target.read_text(encoding="utf-8")
