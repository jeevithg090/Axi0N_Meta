"""P1-09 uploads: transcode to a faststart H.264 chunk, publish it, and reject bad uploads."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rulecam.bus import Topic
from rulecam.capture.pair_phone import MAX_UPLOAD_BYTES, SESSION_HEADER
from rulecam.capture.pair_phone.errors import TranscodeError
from rulecam.config import Settings
from rulecam.contracts import Chunk, Provenance
from tests.capture.pair_phone.conftest import Phone, RecordingBus, make_clip
from tests.capture.test_chunker import top_level_atoms
from tests.fakes import FakeClock


def codec_of(path: str) -> str:
    return subprocess.run(  # noqa: S603
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
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def published_chunks(bus: RecordingBus) -> list[Chunk]:
    chunks = [p for t, p in bus.published if t == Topic.CHUNK_READY]
    assert all(isinstance(c, Chunk) for c in chunks)
    return [c for c in chunks if isinstance(c, Chunk)]


@pytest.mark.parametrize(
    "recording",
    [("libvpx", "video/webm;codecs=vp8"), ("libx264", "video/mp4")],
    ids=["webm", "mp4"],
)
def test_upload_is_transcoded_to_faststart_h264_and_published(
    phone: Phone, bus: RecordingBus, clock: FakeClock, tmp_path: Path, recording: tuple[str, str]
) -> None:
    codec, content_type = recording
    suffix = content_type.split(";")[0].split("/")[1]
    clip = make_clip(tmp_path / f"tiny.{suffix}", seconds=3, codec=codec).read_bytes()

    response = phone.upload(clip, content_type)

    assert response.status_code == 201, response.text
    assert response.json()["chunk_id"] == "phone-1_000000"
    [chunk] = published_chunks(bus)
    assert chunk.camera_id == "phone-1"
    assert (chunk.fps, chunk.n_frames, chunk.width, chunk.height) == (10.0, 30, 320, 240)
    assert chunk.provenance == Provenance.RECORDED
    assert (clock.now() - chunk.start_ts).total_seconds() == pytest.approx(3.0)
    atoms = top_level_atoms(Path(chunk.path))
    assert atoms.index("moov") < atoms.index("mdat"), atoms
    assert codec_of(chunk.path) == "h264"
    assert not list((Path(chunk.path).parent / ".incoming").iterdir()), "raw upload kept"


def test_consecutive_chunks_are_numbered_and_never_overlap(
    phone: Phone, bus: RecordingBus, clock: FakeClock, webm_clip: bytes
) -> None:
    assert phone.upload(webm_clip).status_code == 201
    clock.advance(1.0)  # arrives early: placed right after the previous chunk
    assert phone.upload(webm_clip).status_code == 201

    first, second = published_chunks(bus)

    assert [first.chunk_id, second.chunk_id] == ["phone-1_000000", "phone-1_000001"]
    assert (second.start_ts - first.start_ts).total_seconds() == pytest.approx(3.0)


def test_non_video_upload_is_rejected_with_415(phone: Phone, bus: RecordingBus) -> None:
    response = phone.upload(b"hello, not a video", "text/plain", "notes.txt")

    assert response.status_code == 415
    assert bus.published == []


def test_garbage_declared_as_video_is_rejected_with_415(phone: Phone) -> None:
    assert phone.upload(b"\x1a\x45\xdf\xa3 definitely not webm" * 50).status_code == 415


def test_disallowed_codec_is_rejected_with_415(phone: Phone, tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "mpeg4.mp4", seconds=1, codec="mpeg4").read_bytes()

    response = phone.upload(clip, "video/mp4", "clip.mp4")

    assert response.status_code == 415
    assert "mpeg4" in response.json()["detail"]


def test_oversize_upload_is_rejected_with_413(phone: Phone, bus: RecordingBus) -> None:
    response = phone.upload(b"\0" * (MAX_UPLOAD_BYTES + 1024 * 1024))

    assert response.status_code == 413
    assert bus.published == []


def test_upload_just_over_the_limit_is_rejected_with_413(phone: Phone) -> None:
    assert phone.upload(b"\0" * (MAX_UPLOAD_BYTES + 1)).status_code == 413


def test_oversize_chunked_upload_without_length_is_rejected_with_413(phone: Phone) -> None:
    boundary = "rulecamboundary"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="c.webm"\r\n'
        "Content-Type: video/webm\r\n\r\n"
    ).encode()
    body = head + b"\0" * (MAX_UPLOAD_BYTES + 64 * 1024) + f"\r\n--{boundary}--\r\n".encode()

    response = phone.client.post(
        f"/pair/{phone.token}/chunk",
        headers={**phone.headers, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        content=iter([body]),
    )

    assert response.status_code == 413


def test_clip_longer_than_the_cap_is_rejected_with_413(phone: Phone, tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "long.webm", seconds=8).read_bytes()

    response = phone.upload(clip)

    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


def test_clip_without_a_recorded_duration_is_capped_while_transcoding(
    phone: Phone, bus: RecordingBus, tmp_path: Path
) -> None:
    clip = make_clip(tmp_path / "live.webm", seconds=8, live=True).read_bytes()

    assert phone.upload(clip).status_code == 201

    [chunk] = published_chunks(bus)
    assert chunk.n_frames == 60  # 6 s cap at 10 fps


def test_upload_needs_the_session_key(phone: Phone, webm_clip: bytes) -> None:
    files = {"file": ("c.webm", webm_clip, "video/webm")}
    url = f"/pair/{phone.token}/chunk"

    assert phone.client.post(url, files=files).status_code == 401
    wrong = {SESSION_HEADER: "not-the-session"}
    assert phone.client.post(url, files=files, headers=wrong).status_code == 401


def test_upload_without_a_file_field_is_rejected(phone: Phone) -> None:
    response = phone.client.post(
        f"/pair/{phone.token}/chunk", headers=phone.headers, files={"other": ("a", b"x")}
    )
    assert response.status_code in (400, 422)


def test_uploads_are_rate_limited_to_one_per_second(
    phone: Phone, clock: FakeClock, webm_clip: bytes
) -> None:
    assert phone.upload(webm_clip).status_code == 201

    too_soon = phone.upload(webm_clip)

    assert too_soon.status_code == 429
    assert too_soon.headers["Retry-After"] == "1"
    clock.advance(1.0)
    assert phone.upload(webm_clip).status_code == 201


def test_plain_http_is_rejected_except_on_localhost(
    phone: Phone, make_client: Callable[..., TestClient]
) -> None:
    over_lan = make_client(base_url="http://192.168.1.20:8000")
    over_localhost = make_client(base_url="http://localhost:8000")

    assert over_lan.get(f"/pair/{phone.token}").status_code == 403
    assert over_localhost.get(f"/pair/{phone.token}").status_code == 200


def test_tokens_can_only_be_created_on_the_owners_machine(
    make_client: Callable[..., TestClient],
) -> None:
    through_tunnel = make_client()
    spoofed_host = make_client(base_url="http://localhost:8000")

    assert through_tunnel.post("/pair/token").status_code == 403
    assert spoofed_host.post("/pair/token").status_code == 403


def test_phone_page_is_served_with_strict_headers(phone: Phone) -> None:
    response = phone.client.get(f"/pair/{phone.token}")

    assert response.status_code == 200
    assert "getUserMedia" not in response.text  # behaviour lives in the external script
    assert 'src="assets/phone.js"' in response.text
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"


def test_page_assets_come_from_a_fixed_allowlist(phone_client: TestClient) -> None:
    script = phone_client.get("/pair/assets/phone.js")

    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert 'facingMode: "environment"' in script.text
    assert "audio: false" in script.text
    assert phone_client.get("/pair/assets/phone.css").status_code == 200
    assert phone_client.get("/pair/assets/server.py").status_code == 404


def test_failed_transcode_is_rejected_with_422_and_leaves_no_chunk(
    phone: Phone, bus: RecordingBus, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_ffmpeg(args: list[str], *, timeout_s: float) -> str:
        Path(args[-1]).write_bytes(b"half a chunk")
        raise TranscodeError("ffmpeg crashed")

    monkeypatch.setattr("rulecam.capture.pair_phone.service.run_tool", broken_ffmpeg)

    clip = make_clip(settings.rulecam_data_dir / "ok.webm", seconds=1).read_bytes()

    response = phone.upload(clip)

    assert response.status_code == 422
    assert bus.published == []
    assert not list((settings.rulecam_data_dir / "chunks").glob("phone-1_*.mp4"))


def test_malformed_content_length_is_rejected(phone: Phone) -> None:
    response = phone.client.post(
        f"/pair/{phone.token}/chunk",
        headers={**phone.headers, "Content-Length": "lots", "Content-Type": "video/webm"},
        content=b"",
    )
    assert response.status_code == 400
