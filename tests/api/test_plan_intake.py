"""Tests for planner intake routes: uploads and plan requests (P3-09b)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.api.plans import (
    UPLOAD_TYPES,
    PlanRegistry,
    plans_router,
    save_upload,
    sniff_matches,
)
from rulecam.bus import Bus
from rulecam.contracts import RequestItem
from rulecam.planner.steps.understand import ALLOWED_SUFFIXES

OWNER: dict[str, object] = {"base_url": "http://127.0.0.1", "client": ("127.0.0.1", 5000)}
TUNNEL: dict[str, object] = {"base_url": "https://demo.tunnel.test", "client": ("203.0.113.7", 1)}

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 20
PDF = b"%PDF-1.7\n%..."
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 8
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 4
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8


class FakePlanner:
    def __init__(self) -> None:
        self.requests: list[list[RequestItem]] = []

    def submit(self, items: Sequence[RequestItem]) -> str:
        self.requests.append(list(items))
        return "main-st"


def client(
    tmp_path: Path,
    planner: FakePlanner | None = None,
    *,
    where: dict[str, object] = OWNER,
    max_bytes: int = 1024,
    uploads: bool = True,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        plans_router(
            PlanRegistry(),
            Bus(),
            uploads_dir=tmp_path / "uploads" if uploads else None,
            planner=planner,
            max_upload_bytes=max_bytes,
        )
    )
    return TestClient(app, **where)  # type: ignore[arg-type]


def upload(c: TestClient, name: str, data: bytes, content_type: str) -> int:
    reply = c.post("/plans/uploads", files={"file": (name, data, content_type)})
    return int(reply.status_code)


def test_upload_types_are_ones_the_understand_step_accepts() -> None:
    for kind, ext in UPLOAD_TYPES.values():
        assert f".{ext}" in ALLOWED_SUFFIXES[kind]


def test_upload_stores_content_addressed_file(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        reply = c.post("/plans/uploads", files={"file": ("../../etc/x.wav", WAV, "audio/wav")})
        again = c.post("/plans/uploads", files={"file": ("other.wav", WAV, "audio/x-wav")})
    assert reply.status_code == 201
    body = reply.json()
    assert body["kind"] == "voice"
    assert body["bytes"] == len(WAV)
    assert body["file"].startswith("uploads/")
    assert body["file"].endswith(".wav")
    assert ".." not in body["file"]
    assert again.json()["file"] == body["file"]
    assert [p.name for p in (tmp_path / "uploads").iterdir()] == [body["file"].split("/")[1]]


@pytest.mark.parametrize(
    ("name", "data", "content_type", "kind"),
    [
        ("a.pdf", PDF, "application/pdf", "pdf"),
        ("a.jpg", JPG, "image/jpeg", "photo"),
        ("a.png", PNG, "image/png", "photo"),
        ("a.webp", WEBP, "image/webp", "photo"),
        ("a.mp4", MP4, "video/mp4", "video"),
    ],
)
def test_upload_kinds(tmp_path: Path, name: str, data: bytes, content_type: str, kind: str) -> None:
    with client(tmp_path) as c:
        reply = c.post("/plans/uploads", files={"file": (name, data, content_type)})
    assert reply.status_code == 201
    assert reply.json()["kind"] == kind


def test_upload_rejections(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        assert upload(c, "a.exe", b"MZ", "application/x-msdownload") == 415
        # Types the planner cannot read (Voice Transcribe takes WAV, the Files API MP4).
        assert upload(c, "a.webm", b"\x1aE\xdf\xa3", "audio/webm") == 415
        assert upload(c, "a.mov", MP4, "video/quicktime") == 415
        # The content type is the client's claim: the bytes must agree.
        assert upload(c, "a.pdf", b"<script>", "application/pdf") == 415
        assert upload(c, "a.wav", WEBP, "audio/wav") == 415
        assert upload(c, "a.wav", WAV + b"x" * 2000, "audio/wav") == 413
        assert upload(c, "a.wav", b"", "audio/wav") == 422
    assert not (tmp_path / "uploads").exists() or not list((tmp_path / "uploads").iterdir())


def test_sniff_matches_each_type() -> None:
    samples = {"pdf": PDF, "jpg": JPG, "png": PNG, "webp": WEBP, "wav": WAV, "mp4": MP4}
    for ext, data in samples.items():
        assert sniff_matches(ext, data)
        assert not any(sniff_matches(other, data) for other in samples if other != ext)
    assert not sniff_matches("exe", b"MZ")


def test_uploads_are_owner_only_and_can_be_disabled(tmp_path: Path) -> None:
    with client(tmp_path, where=TUNNEL) as remote:
        assert upload(remote, "a.wav", WAV, "audio/wav") == 403
    with client(tmp_path, uploads=False) as c:
        assert upload(c, "a.wav", WAV, "audio/wav") == 503


def test_plan_requests_go_to_the_planner_with_stored_paths(tmp_path: Path) -> None:
    planner = FakePlanner()
    with client(tmp_path, planner) as c:
        stored = c.post("/plans/uploads", files={"file": ("v.wav", WAV, "audio/wav")}).json()
        body = {
            "items": [
                {"kind": "text", "text": "quiero pasar la próxima inspección", "lang": "es"},
                {"kind": "voice", "file": stored["file"]},
            ]
        }
        reply = c.post("/plans/requests", json=body)
        assert c.post("/plans/requests", json={"items": []}).status_code == 422
    assert reply.status_code == 202
    assert reply.json() == {"plan_id": "main-st"}
    text, voice = planner.requests[0]
    assert text.text == "quiero pasar la próxima inspección"
    assert voice.file == str(tmp_path / "uploads" / stored["file"].removeprefix("uploads/"))
    assert Path(voice.file).read_bytes() == WAV
    with client(tmp_path) as c:
        assert c.post("/plans/requests", json=body).status_code == 503
    with client(tmp_path, planner, where=TUNNEL) as remote:
        assert remote.post("/plans/requests", json=body).status_code == 403


@pytest.mark.parametrize(
    "item",
    [
        {"kind": "pdf", "file": "/etc/hosts.pdf"},
        {"kind": "pdf", "file": "uploads/../../secret.pdf"},
        {"kind": "voice", "file": "uploads/" + "0" * 32 + ".wav"},  # not uploaded
        {"kind": "voice"},
        {"kind": "pdf", "file": "STORED"},  # a stored WAV is not a PDF
    ],
)
def test_plan_requests_only_take_stored_uploads(tmp_path: Path, item: dict[str, str]) -> None:
    planner = FakePlanner()
    with client(tmp_path, planner) as c:
        stored = c.post("/plans/uploads", files={"file": ("v.wav", WAV, "audio/wav")}).json()
        if item.get("file") == "STORED":
            item = {**item, "file": stored["file"]}
        reply = c.post("/plans/requests", json={"items": [item]})
    assert reply.status_code == 422
    assert planner.requests == []


def test_save_upload_is_idempotent(tmp_path: Path) -> None:
    first = save_upload(tmp_path, b"data", "wav")
    second = save_upload(tmp_path, b"data", "wav")
    assert first == second
    assert first.read_bytes() == b"data"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://192.168.1.5:3000"])
def test_owner_writes_reject_cross_site_pages(tmp_path: Path, origin: str) -> None:
    # Any web page can make the owner's browser POST to localhost; its Origin gives it away.
    with client(tmp_path, FakePlanner()) as c:
        c.headers["Origin"] = origin
        assert upload(c, "a.wav", WAV, "audio/wav") == 403
        assert (
            c.post("/plans/requests", json={"items": [{"kind": "text", "text": "x"}]}).status_code
            == 403
        )
        assert c.post("/plans/main-st/approve").status_code == 403
        assert c.get("/plans").status_code == 200


def test_owner_writes_accept_the_local_ui(tmp_path: Path) -> None:
    with client(tmp_path, FakePlanner()) as c:
        c.headers["Origin"] = "http://localhost:3000"
        assert upload(c, "a.wav", WAV, "audio/wav") == 201
