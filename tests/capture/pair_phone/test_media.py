"""P1-09 media and service helpers: hardened ffmpeg arguments, probe parsing, timelines."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest

from rulecam.capture.pair_phone import PairPhone
from rulecam.capture.pair_phone.errors import TranscodeError, UnsupportedMediaError
from rulecam.capture.pair_phone.media import (
    last_frame_args,
    media_kind,
    parse_probe,
    probe_args,
    run_tool,
    transcode_args,
)
from rulecam.capture.pair_phone.service import chunk_start, next_phone_number, page_url
from rulecam.config import Settings
from tests.capture.pair_phone.conftest import FakeFrameSegmenter, RecordingBus
from tests.fakes import EPOCH, FakeClock


@pytest.mark.parametrize(
    ("content_type", "kind"),
    [
        ("video/webm", "webm"),
        ("video/webm;codecs=vp8,opus", "webm"),
        ("Video/MP4; codecs=avc1.42E01E", "mp4"),
    ],
)
def test_media_kind_accepts_webm_and_mp4(content_type: str, kind: str) -> None:
    assert media_kind(content_type) == kind


@pytest.mark.parametrize("content_type", [None, "", "text/plain", "image/png", "video/quicktime"])
def test_media_kind_rejects_everything_else(content_type: str | None) -> None:
    with pytest.raises(UnsupportedMediaError):
        media_kind(content_type)


def probe_json(
    codec: str = "vp8", width: int = 640, height: int = 480, duration: str = "3.0"
) -> str:
    return json.dumps(
        {
            "streams": [{"codec_name": codec, "width": width, "height": height}],
            "format": {"duration": duration},
        }
    )


def test_parse_probe_reads_codec_size_and_duration() -> None:
    info = parse_probe(probe_json(), "webm")
    assert (info.codec, info.width, info.height, info.duration_s) == ("vp8", 640, 480, 3.0)
    assert parse_probe(probe_json(duration="N/A"), "webm").duration_s is None


@pytest.mark.parametrize(
    "stdout",
    [
        "{}",
        "",
        probe_json(codec="h264"),  # H.264 is not a WebM codec
        probe_json(codec="png"),
        probe_json(width=0),
        probe_json(width=8000, height=8000),
    ],
)
def test_parse_probe_rejects_bad_streams(stdout: str) -> None:
    with pytest.raises(UnsupportedMediaError):
        parse_probe(stdout, "webm")


def test_tool_arguments_force_the_demuxer_and_allow_only_local_files() -> None:
    hostile = Path("/data/.incoming/clip.webm; rm -rf ~")
    for args in (
        probe_args(hostile, "webm"),
        transcode_args(
            hostile, Path("out.mp4"), kind="webm", fps=10, chunk_seconds=3, max_seconds=6
        ),
    ):
        assert args[args.index("-i") + 1] == str(hostile)
        assert args[args.index("-protocol_whitelist") + 1] == "file"
        assert args[args.index("-f") + 1] == "webm"
        assert args.index("-f") < args.index("-i")


def test_transcode_reuses_the_chunker_encoder_and_caps_duration() -> None:
    args = transcode_args(
        Path("in.mp4"), Path("out.mp4"), kind="mp4", fps=10, chunk_seconds=3, max_seconds=6
    )

    assert args[:2] == ["ffmpeg", "-nostdin"]
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-g") + 1] == "30"
    assert args[args.index("-t") + 1] == "6"
    assert args[args.index("-movflags") + 1] == "+faststart"
    assert "-an" in args
    assert "-n" in args  # never overwrite an existing chunk
    assert args[-1] == "out.mp4"


def test_last_frame_args_never_read_stdin() -> None:
    args = last_frame_args(Path("c.mp4"), Path("f.jpg"))
    assert args[:2] == ["ffmpeg", "-nostdin"]
    assert args[-1] == "f.jpg"


async def test_run_tool_reports_failures() -> None:
    with pytest.raises(TranscodeError, match="could not start"):
        await run_tool(["rulecam-no-such-tool"], timeout_s=5)
    with pytest.raises(TranscodeError, match="exited with"):
        await run_tool(["ffprobe", "-v", "error", "/no/such/file.webm"], timeout_s=5)


async def test_run_tool_kills_a_process_that_runs_too_long() -> None:
    args = ["ffmpeg", "-nostdin", "-re", "-f", "lavfi", "-i", "testsrc", "-f", "null", "-"]
    started = asyncio.get_running_loop().time()

    with pytest.raises(TranscodeError, match="timed out"):
        await run_tool(args, timeout_s=0.3)

    assert asyncio.get_running_loop().time() - started < 3


def test_next_phone_number_skips_numbers_used_on_disk() -> None:
    assert next_phone_number([]) == 1
    names = ["phone-2_000004.mp4", "phone-11_000000.mp4", "cam1_000001.mp4", "phone-x_1.mp4"]
    assert next_phone_number(names) == 12


def test_a_restarted_service_continues_phone_numbers(tmp_path: Path) -> None:
    chunks = tmp_path / "data" / "chunks"
    chunks.mkdir(parents=True)
    (chunks / "phone-3_000007.mp4").write_bytes(b"")
    service = PairPhone(
        bus=RecordingBus(),
        clock=FakeClock(),
        segmenter=FakeFrameSegmenter(),
        settings=Settings(_env_file=None, rulecam_data_dir=tmp_path / "data"),
    )

    camera, _ = service.claim(service.registry.issue().token, "")

    assert camera.camera_id == "phone-4"


def test_chunk_start_ends_at_arrival_but_never_overlaps_the_previous_chunk() -> None:
    three = timedelta(seconds=3)
    assert chunk_start(EPOCH, three, None) == EPOCH - three
    assert chunk_start(EPOCH, three, EPOCH - timedelta(seconds=10)) == EPOCH - three
    assert chunk_start(EPOCH, three, EPOCH - timedelta(seconds=1)) == EPOCH - timedelta(seconds=1)


def test_page_url_prefers_the_tunnel() -> None:
    assert page_url("https://t.example/", "http://localhost/", "/pair/abc") == (
        "https://t.example/pair/abc"
    )
    assert page_url(None, "http://localhost:8000/", "/pair/abc") == (
        "http://localhost:8000/pair/abc"
    )
