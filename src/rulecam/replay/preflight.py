"""Pre-take checklist (`make preflight`): is everything the demo needs actually up?

Prints a ✓/✗ table and exits non-zero when any required check fails. Hardware that is not
configured (no `KASA_PLUG_HOST`, say) is shown as skipped rather than failed. Every outside
dependency (HTTP, subprocesses, disk, the Kasa probe, time) is injected through `PreflightDeps`,
so tests run every pass and fail path without a network.
"""

from __future__ import annotations

import asyncio
import math
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import numpy as np

from rulecam.config import Settings, get_settings
from rulecam.contracts import Frozen
from rulecam.voice_out.wav import encode_wav

MIN_FREE_BYTES = 10 * 1024**3
CHUNK_FRESH_S = 30.0
HTTP_TIMEOUT_S = 20.0
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_SERVER_ERROR_MIN = 500
TONE_HZ = 880.0
TONE_S = 0.5
TONE_RATE = 16_000
REQUIRED_ENV = ("META_API_KEY", "META_API_BASE_URL", "BASETEN_API_KEY", "HF_TOKEN")
CREDITS_REMINDER = "check credits and usage alerts on dev.meta.ai"

SUBPROCESS_TIMEOUT_S = 60
EXIT_NOT_FOUND = 127
EXIT_TIMED_OUT = 124  # what coreutils `timeout` returns

Runner = Callable[[Sequence[str]], int]


class CheckResult(Frozen):
    """One line of the preflight table.

    Attributes:
        name: Short check name.
        ok: Whether it passed (a skipped check counts as passed).
        required: Whether a failure makes preflight exit non-zero.
        detail: What was found; never contains secret values.
        skipped: The check did not apply (hardware not configured).
    """

    name: str
    ok: bool
    required: bool = True
    detail: str = ""
    skipped: bool = False


def _run_subprocess(argv: Sequence[str]) -> int:
    """Run a fixed argument list (never a shell) and return its exit code."""
    executable = shutil.which(argv[0])
    if executable is None:
        return EXIT_NOT_FOUND
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv from this module, no shell
            [executable, *argv[1:]], check=False, capture_output=True, timeout=SUBPROCESS_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return EXIT_TIMED_OUT
    return completed.returncode


def _probe_kasa(host: str) -> bool:
    """Return whether a Kasa device answers at `host`.

    Raises:
        OSError: On a network failure (python-kasa's own errors are re-raised as this).
    """
    from kasa import Discover, KasaException  # noqa: PLC0415 - optional hardware dependency

    async def probe() -> bool:
        device = await Discover.discover_single(host, discovery_timeout=5)
        if device is None:
            return False
        await device.update()
        await device.disconnect()
        return True

    try:
        return asyncio.run(probe())
    except KasaException as exc:
        msg = f"Kasa device at {host}: {type(exc).__name__}"
        raise OSError(msg) from exc


def _newest_mtime(directory: Path) -> float | None:
    if not directory.is_dir():
        return None
    mtimes = [p.stat().st_mtime for p in directory.rglob("*.mp4")]
    return max(mtimes, default=None)


def _free_bytes(path: Path) -> int:
    target = path if path.exists() else Path()
    return shutil.disk_usage(target).free


@dataclass
class PreflightDeps:
    """Everything preflight touches outside the process, injectable for tests."""

    settings: Settings
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=HTTP_TIMEOUT_S))
    run: Runner = _run_subprocess
    probe_kasa: Callable[[str], bool] = _probe_kasa
    free_bytes: Callable[[Path], int] = _free_bytes
    newest_chunk_mtime: Callable[[Path], float | None] = _newest_mtime
    now: Callable[[], float] = time.time
    player: str = field(
        default_factory=lambda: "afplay" if platform.system() == "Darwin" else "aplay"
    )


def check_env(deps: PreflightDeps) -> CheckResult:
    """Required API keys and URLs are set (names only are reported)."""
    s = deps.settings
    present = {
        "META_API_KEY": s.meta_api_key is not None,
        "META_API_BASE_URL": bool(s.meta_api_base_url),
        "BASETEN_API_KEY": s.baseten_api_key is not None,
        "HF_TOKEN": s.hf_token is not None,
    }
    missing = [name for name in REQUIRED_ENV if not present[name]]
    detail = "missing: " + ", ".join(missing) if missing else "all set"
    return CheckResult(name="env keys", ok=not missing, detail=detail)


def check_meta_api(deps: PreflightDeps) -> CheckResult:
    """The Meta Model API answers and accepts the key."""
    s = deps.settings
    if not s.meta_api_base_url or s.meta_api_key is None:
        return CheckResult(name="Meta API", ok=False, detail="META_API_BASE_URL/KEY not set")
    headers = {"Authorization": f"Bearer {s.meta_api_key.get_secret_value()}"}
    try:
        status = deps.http.get(s.meta_api_base_url, headers=headers).status_code
    except httpx.HTTPError as exc:
        return CheckResult(name="Meta API", ok=False, detail=f"unreachable: {type(exc).__name__}")
    if status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
        return CheckResult(name="Meta API", ok=False, detail=f"key rejected (HTTP {status})")
    ok = status < HTTP_SERVER_ERROR_MIN
    return CheckResult(name="Meta API", ok=ok, detail=f"HTTP {status}; {CREDITS_REMINDER}")


def check_spark_open(deps: PreflightDeps) -> CheckResult:
    """Baseten Spark Open answers a tiny prompt with `reasoning_content`."""
    s = deps.settings
    if s.baseten_api_key is None:
        return CheckResult(name="Spark Open", ok=False, detail="BASETEN_API_KEY not set")
    body = {
        "model": s.baseten_spark_open_model,
        "messages": [{"role": "user", "content": "Reply with the word OK."}],
        "max_tokens": 256,
    }
    headers = {"Authorization": f"Api-Key {s.baseten_api_key.get_secret_value()}"}
    url = f"{s.baseten_base_url.rstrip('/')}/chat/completions"
    try:
        response = deps.http.post(url, json=body, headers=headers)
        message = response.json()["choices"][0]["message"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        return CheckResult(name="Spark Open", ok=False, detail=f"bad reply: {type(exc).__name__}")
    reasoning = message.get("reasoning_content") if isinstance(message, dict) else None
    if not reasoning:
        return CheckResult(name="Spark Open", ok=False, detail="no reasoning_content in reply")
    return CheckResult(name="Spark Open", ok=True, detail=f"{s.baseten_spark_open_model}")


def check_glimmer(deps: PreflightDeps) -> CheckResult:
    """The local llama-server answers `GET /v1/models` with at least one model."""
    url = f"{deps.settings.glimmer_base_url.rstrip('/')}/models"
    try:
        data = deps.http.get(url).json()["data"]
        names = [str(item["id"]) for item in data]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return CheckResult(name="Glimmer", ok=False, detail=f"not serving: {type(exc).__name__}")
    if not names:
        return CheckResult(name="Glimmer", ok=False, detail="no model loaded")
    return CheckResult(name="Glimmer", ok=True, detail=", ".join(names))


def check_modal(deps: PreflightDeps) -> CheckResult:
    """The Modal CLI is logged in and lists the deployed apps."""
    code = deps.run(["modal", "app", "list"])
    if code == EXIT_NOT_FOUND:
        return CheckResult(name="Modal", ok=False, detail="modal CLI not installed")
    if code == EXIT_TIMED_OUT:
        return CheckResult(name="Modal", ok=False, detail="modal app list timed out")
    detail = "apps listed; warm the SAM worker before the take" if code == 0 else f"exit {code}"
    return CheckResult(name="Modal", ok=code == 0, detail=detail)


def check_cameras(deps: PreflightDeps) -> CheckResult:
    """Some camera wrote a chunk in the last 30 s."""
    chunk_dir = deps.settings.rulecam_data_dir / "chunks"
    newest = deps.newest_chunk_mtime(chunk_dir)
    if newest is None:
        return CheckResult(name="cameras", ok=False, detail=f"no chunks under {chunk_dir}")
    age = deps.now() - newest
    ok = age <= CHUNK_FRESH_S
    return CheckResult(name="cameras", ok=ok, detail=f"newest chunk {age:.0f} s old")


def _check_kasa(deps: PreflightDeps, name: str, host: str | None, env: str) -> CheckResult:
    if not host:
        return CheckResult(name=name, ok=True, skipped=True, detail=f"{env} not set")
    try:
        found = deps.probe_kasa(host)
    except (OSError, TimeoutError, RuntimeError) as exc:
        return CheckResult(name=name, ok=False, detail=f"{host}: {type(exc).__name__}")
    return CheckResult(name=name, ok=found, detail=host if found else f"{host}: no answer")


def check_plug(deps: PreflightDeps) -> CheckResult:
    """The interlock smart plug answers."""
    return _check_kasa(deps, "Kasa plug", deps.settings.kasa_plug_host, "KASA_PLUG_HOST")


def check_light(deps: PreflightDeps) -> CheckResult:
    """The stack light / RGB bulb answers."""
    return _check_kasa(deps, "Kasa light", deps.settings.kasa_light_host, "KASA_LIGHT_HOST")


def tone_wav() -> bytes:
    """Return a short 880 Hz test tone as WAV bytes."""
    t = np.arange(int(TONE_RATE * TONE_S)) / TONE_RATE
    return encode_wav(0.3 * np.sin(2 * math.pi * TONE_HZ * t), TONE_RATE)


def check_speaker(deps: PreflightDeps) -> CheckResult:
    """Play a test tone; a person must confirm they heard it."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tone.wav"
        path.write_bytes(tone_wav())
        code = deps.run([deps.player, str(path)])
    ok = code == 0
    detail = "tone played: did you hear it?" if ok else f"{deps.player} exit {code}"
    return CheckResult(name="speaker", ok=ok, required=False, detail=detail)


def check_disk(deps: PreflightDeps) -> CheckResult:
    """At least 10 GB free where data is written."""
    free = deps.free_bytes(deps.settings.rulecam_data_dir)
    return CheckResult(
        name="disk", ok=free >= MIN_FREE_BYTES, detail=f"{free / 1024**3:.1f} GB free"
    )


CHECKS: tuple[Callable[[PreflightDeps], CheckResult], ...] = (
    check_env,
    check_meta_api,
    check_spark_open,
    check_glimmer,
    check_modal,
    check_cameras,
    check_plug,
    check_light,
    check_speaker,
    check_disk,
)


def run_preflight(deps: PreflightDeps) -> list[CheckResult]:
    """Run every check in order.

    Args:
        deps: The injected outside world.

    Returns:
        One result per check.
    """
    return [check(deps) for check in CHECKS]


def render_table(results: Sequence[CheckResult]) -> str:
    """Format results as a ✓/✗ table (- for skipped, ! for an optional failure)."""
    width = max(len(r.name) for r in results)
    lines = []
    for r in results:
        mark = "-" if r.skipped else "✓" if r.ok else "✗" if r.required else "!"
        lines.append(f"{mark} {r.name.ljust(width)}  {r.detail}")
    return "\n".join(lines)


def exit_code(results: Sequence[CheckResult]) -> int:
    """Return 1 when any required check failed, else 0."""
    return int(any(r.required and not r.ok for r in results))


def main() -> int:
    """Run preflight against the real environment and print the table."""
    results = run_preflight(PreflightDeps(settings=get_settings()))
    sys.stdout.write(render_table(results) + "\n")
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
