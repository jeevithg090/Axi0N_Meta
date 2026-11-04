"""No orphan ffmpeg: it dies with a crashed parent, and a stale one is reaped on startup."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rulecam.connectors.process import (
    PIDFILE,
    command_line,
    reap_stale,
    spawn,
    write_pidfile,
)

CRASHING_OWNER = """
import sys, time
from pathlib import Path
from rulecam.bus import Bus
from rulecam.clock import SystemClock
from rulecam.connectors.sources import ResolvedInput, SourceKind, input_args
from rulecam.connectors.stream import StreamConfig, StreamWorker

clip, out = sys.argv[1], Path(sys.argv[2])
worker = StreamWorker(
    StreamConfig(camera_id="cam-1", out_dir=out),
    lambda: ResolvedInput(args=input_args(SourceKind.FILE, clip)),
    bus=Bus(),
    clock=SystemClock(),
)
worker.start()
while worker._process is None:
    time.sleep(0.05)
print(f"PID={worker._process.pid}", flush=True)
time.sleep(60)
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return command_line(pid) is not None


def wait_gone(pid: int, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return False


def test_ffmpeg_dies_when_its_owner_is_killed(clip: Path, tmp_path: Path) -> None:
    owner = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", CRASHING_OWNER, str(clip), str(tmp_path / "cam-1")],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert owner.stdout is not None
    line = ""
    while not line.startswith("PID="):
        line = owner.stdout.readline()
        assert line, "the owner process exited early"
    ffmpeg = int(line.removeprefix("PID="))
    assert alive(ffmpeg)
    assert (tmp_path / "cam-1" / PIDFILE).read_text() == str(ffmpeg)
    owner.send_signal(signal.SIGKILL)  # a crash: no shutdown code runs
    owner.wait()
    try:
        assert wait_gone(ffmpeg), "ffmpeg outlived its crashed owner"
    finally:
        if alive(ffmpeg):
            os.kill(ffmpeg, signal.SIGKILL)


def test_spawned_ffmpeg_leads_its_own_group(tmp_path: Path) -> None:
    child = spawn(["sleep", "5"])
    try:
        assert os.getpgid(child.process.pid) == child.process.pid
        assert os.getpgid(child.process.pid) != os.getpgid(0)
    finally:
        child.process.terminate()
        child.process.wait()
        child.release()


def start_sleeper(*marker: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603
        ["/bin/sh", "-c", "sleep 30; true", *marker],  # no exec: keeps the marker
        start_new_session=True,
    )


@pytest.mark.parametrize("mentions_folder", [True, False])
def test_reap_stale_kills_only_that_cameras_ffmpeg(tmp_path: Path, mentions_folder: bool) -> None:
    folder = tmp_path / "cam-7"
    folder.mkdir()
    stale = start_sleeper(*([str(folder)] if mentions_folder else ["other"]))
    try:
        time.sleep(0.2)
        write_pidfile(folder, stale.pid)
        assert reap_stale(folder) is mentions_folder
        assert not (folder / PIDFILE).exists()
        if mentions_folder:
            stale.wait(timeout=5)
        else:
            assert stale.poll() is None
    finally:
        if stale.poll() is None:
            stale.kill()
            stale.wait()


def test_reap_stale_without_or_with_a_dead_pid(tmp_path: Path) -> None:
    assert reap_stale(tmp_path) is False
    gone = subprocess.Popen(["true"])  # noqa: S607
    gone.wait()
    write_pidfile(tmp_path, gone.pid)
    assert reap_stale(tmp_path) is False
    (tmp_path / PIDFILE).write_text("not a pid")
    assert reap_stale(tmp_path) is False
