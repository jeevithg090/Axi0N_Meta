"""ffmpeg processes that never outlive Rulecam, and cleanup of any that did.

A camera's ffmpeg must stop when the API stops, however it stops. A clean shutdown stops it
through the connectors router's lifespan; this module covers a crash or `kill -9`:

- Each ffmpeg starts in its **own process group** (a new session), so a terminal's Ctrl-C or a
  signal to the API's group does not hit it half-way, and the whole group can be killed at once.
- **Linux**: the child asks the kernel for a parent-death signal (`prctl(PR_SET_PDEATHSIG,
  SIGTERM)`) between fork and exec, so it gets SIGTERM the moment the thread that started it
  dies (a camera's supervisor thread lives as long as the camera runs).
- **Elsewhere (macOS)**: a tiny watchdog (`/bin/sh` running `cat`) holds the read end of a pipe
  whose write end only Rulecam holds. When Rulecam dies the pipe closes, `cat` returns and the
  watchdog kills ffmpeg's process group (SIGTERM, then SIGKILL after 3 s).
- **Startup**: every ffmpeg's pid is written next to its chunks (`.ffmpeg.pid`); before a camera
  starts, and for every camera folder when the connectors start, a pid that is still alive and
  is still that camera's ffmpeg (its command line names the camera's chunk folder) is killed.
  A pid that cannot be checked is left alone.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rulecam.log import get_logger

_log = get_logger(__name__)

PIDFILE: Final = ".ffmpeg.pid"
PR_SET_PDEATHSIG: Final = 1
REAP_WAIT_S: Final = 3.0
WATCHDOG_SCRIPT: Final = (
    'cat >/dev/null; kill -TERM -- -"$1" 2>/dev/null; sleep 3; kill -KILL -- -"$1" 2>/dev/null'
)


def _load_prctl() -> Callable[..., int] | None:
    """The C library's `prctl`, loaded once in the parent (the child must not load any)."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    except OSError:
        return None
    prctl: Callable[..., int] = libc.prctl
    return prctl


_PRCTL: Final = _load_prctl()


def pdeathsig_available() -> bool:
    """Whether this platform gives children a parent-death signal (Linux)."""
    return _PRCTL is not None


def _die_with_parent(parent: int) -> Callable[[], None]:
    prctl = _PRCTL

    def preexec() -> None:  # runs in the child between fork and exec: no imports, no locks
        if prctl is not None:
            prctl(PR_SET_PDEATHSIG, int(signal.SIGTERM))
        if os.getppid() != parent:  # the parent died before the signal was armed
            os._exit(1)

    return preexec


@dataclass
class Child:
    """A started ffmpeg and, off Linux, its watchdog."""

    process: subprocess.Popen[bytes]
    watchdog: subprocess.Popen[bytes] | None = None

    def release(self) -> None:
        """After ffmpeg has exited: let the watchdog go (closing its pipe ends it)."""
        dog = self.watchdog
        if dog is None:
            return
        if dog.stdin is not None:
            with contextlib.suppress(OSError):
                dog.stdin.close()
        try:
            dog.wait(timeout=REAP_WAIT_S + 2)
        except subprocess.TimeoutExpired:
            dog.kill()


def spawn(args: Sequence[str]) -> Child:
    """Start ffmpeg in its own process group, tied to this process's life.

    Args:
        args: The ffmpeg command (never run through a shell).

    Returns:
        The child, with stdout and stderr piped.

    Raises:
        OSError: When ffmpeg cannot be started.
    """
    use_prctl = pdeathsig_available()
    process = subprocess.Popen(  # noqa: S603 - argument list, never a shell
        list(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        preexec_fn=_die_with_parent(os.getpid()) if use_prctl else None,  # noqa: PLW1509
    )
    if use_prctl:
        return Child(process)
    shell = shutil.which("sh") or "/bin/sh"
    try:
        watchdog = subprocess.Popen(  # noqa: S603 - fixed script, the pgid is an argument
            [shell, "-c", WATCHDOG_SCRIPT, "rulecam-ffmpeg-watchdog", str(process.pid)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        _log.warning("ffmpeg_watchdog_unavailable", pid=process.pid)
        return Child(process)
    return Child(process, watchdog)


def write_pidfile(folder: Path, pid: int) -> None:
    """Remember a camera's ffmpeg pid next to its chunks."""
    (folder / PIDFILE).write_text(str(pid), "utf-8")


def clear_pidfile(folder: Path) -> None:
    """Forget it once ffmpeg has exited."""
    (folder / PIDFILE).unlink(missing_ok=True)


def command_line(pid: int) -> str | None:
    """A live process's command line, or `None` when it is gone or cannot be read."""
    proc = Path(f"/proc/{pid}/cmdline")
    if proc.exists():
        try:
            return proc.read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            return None
    ps = shutil.which("ps")
    if ps is None:
        return None
    result = subprocess.run(  # noqa: S603 - fixed program, the pid is an argument
        [ps, "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reap_stale(folder: Path) -> bool:
    """Kill a previous run's ffmpeg for this camera folder, if one is still running.

    Args:
        folder: The camera's chunk folder.

    Returns:
        Whether a stale ffmpeg was found and stopped.
    """
    path = folder / PIDFILE
    try:
        pid = int(path.read_text("utf-8").strip())
    except (OSError, ValueError):
        return False
    stale = _alive(pid) and str(folder) in (command_line(pid) or "") and pid != os.getpid()
    if stale:
        _log.warning("ffmpeg_stale_reaped", pid=pid, folder=folder.name)
        _signal_group(pid, signal.SIGTERM)
        deadline = time.monotonic() + REAP_WAIT_S
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _alive(pid):
            _signal_group(pid, signal.SIGKILL)
    path.unlink(missing_ok=True)
    return stale


def _signal_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)  # ffmpeg leads its own group
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, sig)
