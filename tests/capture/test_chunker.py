"""Tests for task P1-01: capture service and 3-second chunker."""

from __future__ import annotations

import asyncio
import struct
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rulecam.bus import Bus, Topic
from rulecam.capture import Chunker, ffmpeg_args, input_args, probe_chunk
from rulecam.capture.errors import CaptureError
from rulecam.cli import app
from rulecam.contracts import Chunk
from tests.fakes import EPOCH, FakeClock


def make_test_video(path: Path, seconds: float, fps: int = 10) -> Path:
    """Render an ffmpeg `testsrc` clip (no network, deterministic)."""
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x240:rate={fps}",
            "-t",
            f"{seconds}",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
    )
    return path


def top_level_atoms(path: Path) -> list[str]:
    """Return the top-level MP4 box types in file order."""
    data = path.read_bytes()
    atoms, offset = [], 0
    while offset + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[offset : offset + 8])
        if size == 1:
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
        atoms.append(kind.decode("latin-1"))
        if size == 0:
            break
        offset += size
    return atoms


@pytest.fixture
def seven_second_clip(tmp_path: Path) -> Path:
    return make_test_video(tmp_path / "input.mp4", seconds=7)


async def run_to_completion(chunker: Chunker, bus: Bus) -> list[Chunk]:
    received: list[Chunk] = []

    async def listen() -> None:
        async for message in bus.subscribe(Topic.CHUNK_READY):
            assert isinstance(message.payload, Chunk)
            received.append(message.payload)

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0)
    await asyncio.wait_for(chunker.run(), timeout=30)
    await asyncio.sleep(0)
    listener.cancel()
    return received


async def test_seven_second_clip_gives_two_full_chunks_and_one_partial(
    seven_second_clip: Path, tmp_path: Path
) -> None:
    bus = Bus()
    chunker = Chunker(
        "cam1", str(seven_second_clip), tmp_path / "out", bus=bus, clock=FakeClock(), fps=10
    )

    chunks = await run_to_completion(chunker, bus)

    assert [c.n_frames for c in chunks] == [30, 30, 10]
    assert [c.chunk_id for c in chunks] == ["cam1_000000", "cam1_000001", "cam1_000002"]
    assert all(c.fps == 10.0 and (c.width, c.height) == (320, 240) for c in chunks)
    assert [(c.start_ts - EPOCH).total_seconds() for c in chunks] == [0.0, 3.0, 6.0]


async def test_each_chunk_is_published_exactly_once(
    seven_second_clip: Path, tmp_path: Path
) -> None:
    bus = Bus()
    chunker = Chunker("cam1", str(seven_second_clip), tmp_path / "out", bus=bus, clock=FakeClock())

    chunks = await run_to_completion(chunker, bus)

    assert len({c.chunk_id for c in chunks}) == len(chunks) == 3


async def test_chunks_are_h264_with_moov_before_mdat(
    seven_second_clip: Path, tmp_path: Path
) -> None:
    bus = Bus()
    chunker = Chunker("cam1", str(seven_second_clip), tmp_path / "out", bus=bus, clock=FakeClock())

    chunks = await run_to_completion(chunker, bus)

    for chunk in chunks:
        atoms = top_level_atoms(Path(chunk.path))
        assert atoms.index("moov") < atoms.index("mdat"), atoms
        codec = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                chunk.path,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert codec == "h264"


async def test_stop_terminates_ffmpeg_within_two_seconds(tmp_path: Path) -> None:
    long_clip = make_test_video(tmp_path / "long.mp4", seconds=30)
    bus = Bus()
    chunker = Chunker(
        "cam1", str(long_clip), tmp_path / "out", bus=bus, clock=FakeClock(), realtime=True
    )
    task = asyncio.create_task(chunker.run())
    await asyncio.sleep(1.0)

    started = time.monotonic()
    await chunker.stop()
    await asyncio.wait_for(task, timeout=3)

    assert time.monotonic() - started < 2.0


async def test_stop_before_run_is_a_no_op(tmp_path: Path) -> None:
    chunker = Chunker("cam1", "x.mp4", tmp_path, bus=Bus(), clock=FakeClock())
    await chunker.stop()


def test_hostile_source_is_passed_as_one_argument_never_through_a_shell() -> None:
    hostile = "clip.mp4; rm -rf ~ && echo pwned"
    args = ffmpeg_args(hostile, Path("out/cam1_%06d.mp4"), fps=10, chunk_seconds=3)
    assert args[args.index("-i") + 1] == hostile
    assert args.count(hostile) == 1


async def test_chunker_never_uses_a_shell(
    monkeypatch: pytest.MonkeyPatch, seven_second_clip: Path, tmp_path: Path
) -> None:
    async def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("create_subprocess_shell must never be used")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden)
    bus = Bus()
    chunker = Chunker("cam1", str(seven_second_clip), tmp_path / "out", bus=bus, clock=FakeClock())
    assert len(await run_to_completion(chunker, bus)) == 3


async def test_missing_source_raises_capture_error(tmp_path: Path) -> None:
    chunker = Chunker(
        "cam1", str(tmp_path / "does-not-exist.mp4"), tmp_path / "out", bus=Bus(), clock=FakeClock()
    )
    with pytest.raises(CaptureError):
        await asyncio.wait_for(chunker.run(), timeout=10)


@pytest.mark.parametrize(
    ("source", "platform", "expected"),
    [
        (0, "darwin", ["-f", "avfoundation", "-framerate", "30", "-i", "0:none"]),
        (1, "linux", ["-f", "v4l2", "-i", "/dev/video1"]),
        (
            "rtsp://cam.local/stream",
            "linux",
            ["-rtsp_transport", "tcp", "-i", "rtsp://cam.local/stream"],
        ),
        ("clip.mp4", "linux", ["-i", "clip.mp4"]),
    ],
)
def test_input_args_per_source(source: str | int, platform: str, expected: list[str]) -> None:
    assert input_args(source, platform=platform) == expected


def test_realtime_reads_files_at_native_speed() -> None:
    assert input_args("clip.mp4", realtime=True)[:1] == ["-re"]


def test_keyframes_are_forced_on_chunk_boundaries() -> None:
    args = ffmpeg_args("clip.mp4", Path("o/c_%06d.mp4"), fps=10, chunk_seconds=3)
    assert args[args.index("-g") + 1] == "30"
    assert args[args.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*3)"
    assert "movflags=+faststart" in args


def test_probe_chunk_reads_frames_size_and_rate(seven_second_clip: Path) -> None:
    info = probe_chunk(seven_second_clip)
    assert (info.n_frames, info.width, info.height, info.fps) == (70, 320, 240, 10.0)


def test_probe_chunk_rejects_non_video(tmp_path: Path) -> None:
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    with pytest.raises(CaptureError):
        probe_chunk(junk)


def test_cli_capture_writes_chunks(seven_second_clip: Path, tmp_path: Path) -> None:
    out = tmp_path / "cli-out"
    result = CliRunner().invoke(
        app, ["capture", "cam9", str(seven_second_clip), "--out-dir", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.glob("*.mp4")) == [
        "cam9_000000.mp4",
        "cam9_000001.mp4",
        "cam9_000002.mp4",
    ]
