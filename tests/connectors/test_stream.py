"""The stream worker: real ffmpeg on a rendered clip; chunks, health, reconnects, numbering."""

from __future__ import annotations

import random
import socket
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from rulecam.bus import Bus, Topic
from rulecam.clock import SystemClock
from rulecam.connectors import stream
from rulecam.connectors.errors import SourceError
from rulecam.connectors.sources import ResolvedInput, SourceKind, input_args
from rulecam.connectors.stream import (
    StreamConfig,
    StreamWorker,
    backoff_s,
    error_lines,
    ffmpeg_command,
    parse_input,
    segment_index,
)
from rulecam.contracts import Chunk, Provenance


class RecordingBus(Bus):
    """Keeps every published chunk."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[Chunk] = []

    def publish(self, topic: Topic, payload: object) -> None:
        if topic is Topic.CHUNK_READY and isinstance(payload, Chunk):
            self.chunks.append(payload)


def wait_for(check: Callable[[], bool], timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError("condition not met in time")


def config(out: Path, **changes: object) -> StreamConfig:
    base: dict[str, object] = {
        "camera_id": "cam-1",
        "out_dir": out,
        "provenance": Provenance.UPLOAD,
        "chunk_seconds": 1.0,
    }
    return StreamConfig(**{**base, **changes})  # type: ignore[arg-type]


def test_pure_helpers() -> None:
    assert segment_index(Path("cam-1_000042.mp4")) == 42
    assert segment_index(Path("other.mp4")) is None
    info = parse_input(
        "[info]   Stream #0:0: Video: hevc (Main), yuv420p(tv), 2560x1440, 25 fps, 25 tbr, 90k tbn"
    )
    assert info is not None
    assert (info.codec, info.width, info.height, info.fps) == ("hevc", 2560, 1440, 25.0)
    assert parse_input("[info] Input #0, rtsp, from 'rtsp://***@cam/x':") is None
    lines = [
        "[info] Input #0",
        "[in#0 @ 0x1] [error] method DESCRIBE failed: 401 Unauthorized",
        "[error] Error opening input file rtsp://***@cam/x.",
    ]
    assert error_lines(lines) == (
        "method DESCRIBE failed: 401 Unauthorized | Error opening input file rtsp://***@cam/x."
    )
    assert error_lines(["[info] a", "[info] b"]) == "a | b"
    rng = random.Random(1)  # noqa: S311 - jitter only
    assert 0.8 <= backoff_s(0, rng) <= 1.2
    assert 24.0 <= backoff_s(99, rng) <= 36.0


def test_ffmpeg_command_scales_and_numbers(tmp_path: Path) -> None:
    args = ffmpeg_command(config(tmp_path, max_width=640), ["-i", "x.mp4"], 7)
    assert args[args.index("-segment_start_number") + 1] == "7"
    assert "scale=w='min(640,iw)':h=-2" in args
    assert args[-1].endswith("cam-1_%06d.mp4")
    assert "-progress" in args


def test_clip_played_once_publishes_chunks_and_ends(clip: Path, tmp_path: Path) -> None:
    bus = RecordingBus()
    worker = StreamWorker(
        config(tmp_path, ends=True),
        lambda: ResolvedInput(args=input_args(SourceKind.FILE, str(clip), loop=False)),
        bus=bus,
        clock=SystemClock(),
    )
    worker.start()
    wait_for(lambda: worker.health().state == "ended")
    health = worker.health()
    assert len(bus.chunks) >= 2
    assert health.chunks == len(bus.chunks)
    assert health.input is not None
    assert health.input.codec == "h264"
    assert health.first_chunk_s is not None
    assert health.errors == 0
    chunk = bus.chunks[0]
    assert chunk.camera_id == "cam-1"
    assert chunk.provenance is Provenance.UPLOAD
    assert chunk.fps == pytest.approx(10.0)
    starts = [c.start_ts for c in bus.chunks]
    assert starts == sorted(starts)
    assert worker.latest_chunk() == bus.chunks[-1]


def test_looped_clip_runs_until_stopped_and_numbering_continues(clip: Path, tmp_path: Path) -> None:
    def make() -> tuple[RecordingBus, StreamWorker]:
        bus = RecordingBus()
        worker = StreamWorker(
            config(tmp_path, keep_chunks=50),
            lambda: ResolvedInput(args=input_args(SourceKind.FILE, str(clip))),
            bus=bus,
            clock=SystemClock(),
        )
        return bus, worker

    bus, worker = make()
    worker.start()
    wait_for(lambda: len(bus.chunks) >= 2)
    assert worker.health().state == "live"
    worker.stop()
    assert worker.health().state == "stopped"
    last = max(segment_index(Path(c.path)) or 0 for c in bus.chunks)
    bus2, worker2 = make()
    worker2.start()
    wait_for(lambda: len(bus2.chunks) >= 1)
    worker2.stop()
    assert min(segment_index(Path(c.path)) or 0 for c in bus2.chunks) > last
    ids = [c.chunk_id for c in bus.chunks + bus2.chunks]
    assert len(ids) == len(set(ids))


def test_retention_keeps_newest_files(clip: Path, tmp_path: Path) -> None:
    bus = RecordingBus()
    worker = StreamWorker(
        config(tmp_path, keep_chunks=2, ends=True),
        lambda: ResolvedInput(args=input_args(SourceKind.FILE, str(clip), loop=False)),
        bus=bus,
        clock=SystemClock(),
    )
    worker.start()
    wait_for(lambda: worker.health().state == "ended")
    assert len(list(tmp_path.glob("cam-1_*.mp4"))) <= 2


def test_resolver_errors_back_off_then_connect(
    clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream, "BACKOFF_S", (0.05,))
    attempts: list[int] = []

    def resolve() -> ResolvedInput:
        attempts.append(1)
        if len(attempts) < 3:
            msg = "camera unreachable"
            raise SourceError(msg)
        return ResolvedInput(args=input_args(SourceKind.FILE, str(clip)))

    bus = RecordingBus()
    worker = StreamWorker(config(tmp_path), resolve, bus=bus, clock=SystemClock(), seed=1)
    worker.start()
    wait_for(lambda: len(bus.chunks) >= 1)
    health = worker.health()
    worker.stop()
    assert len(attempts) == 3
    assert health.errors == 2
    assert health.restarts == 2
    assert health.state == "live"
    assert health.last_error is None


def test_ffmpeg_failure_is_reported_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream, "BACKOFF_S", (5.0,))
    missing = tmp_path / "s3cret-name.mp4"
    worker = StreamWorker(
        config(tmp_path),
        lambda: ResolvedInput(args=["-i", str(missing)], secrets=("s3cret-name",)),
        bus=RecordingBus(),
        clock=SystemClock(),
    )
    worker.start()
    wait_for(lambda: worker.health().errors >= 1)
    health = worker.health()
    worker.stop()
    assert health.last_error is not None
    assert health.last_error.startswith("ffmpeg exited")
    assert "s3cret-name" not in health.last_error
    assert health.state in ("reconnecting", "stopped")


def test_silent_camera_is_killed_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream, "BACKOFF_S", (5.0,))
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    worker = StreamWorker(
        config(tmp_path, connect_timeout_s=1.0),
        lambda: ResolvedInput(args=input_args(SourceKind.RTSP, f"rtsp://127.0.0.1:{port}/x")),
        bus=RecordingBus(),
        clock=SystemClock(),
    )
    try:
        worker.start()
        wait_for(lambda: worker.health().errors >= 1)
        assert worker.health().last_error == "no video after 1 s"
    finally:
        worker.stop()
        server.close()
