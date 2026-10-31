"""Tests for planner intake routes: uploads and plan requests (P3-09b)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.api.plans import PlanRegistry, plans_router, save_upload
from rulecam.bus import Bus
from rulecam.contracts import RequestItem

OWNER: dict[str, object] = {"base_url": "http://127.0.0.1", "client": ("127.0.0.1", 5000)}
TUNNEL: dict[str, object] = {"base_url": "https://demo.tunnel.test", "client": ("203.0.113.7", 1)}


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


def test_upload_stores_content_addressed_file(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        reply = c.post(
            "/plans/uploads", files={"file": ("../../etc/x.webm", b"voice", "audio/webm")}
        )
        again = c.post("/plans/uploads", files={"file": ("other.webm", b"voice", "audio/webm")})
    assert reply.status_code == 201
    body = reply.json()
    assert body["kind"] == "voice"
    assert body["bytes"] == 5
    assert body["file"].startswith("uploads/")
    assert body["file"].endswith(".webm")
    assert ".." not in body["file"]
    assert again.json()["file"] == body["file"]
    assert [p.name for p in (tmp_path / "uploads").iterdir()] == [body["file"].split("/")[1]]


def test_upload_kinds_and_rejections(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        assert (
            c.post("/plans/uploads", files={"file": ("a.pdf", b"%PDF", "application/pdf")}).json()[
                "kind"
            ]
            == "pdf"
        )
        assert (
            c.post("/plans/uploads", files={"file": ("a.jpg", b"jpg", "image/jpeg")}).json()["kind"]
            == "photo"
        )
        assert (
            c.post("/plans/uploads", files={"file": ("a.mp4", b"vid", "video/mp4")}).json()["kind"]
            == "video"
        )
        assert (
            c.post(
                "/plans/uploads", files={"file": ("a.exe", b"MZ", "application/x-msdownload")}
            ).status_code
            == 415
        )
        assert (
            c.post(
                "/plans/uploads", files={"file": ("a.wav", b"x" * 2000, "audio/wav")}
            ).status_code
            == 413
        )
        assert (
            c.post("/plans/uploads", files={"file": ("a.wav", b"", "audio/wav")}).status_code == 422
        )


def test_uploads_are_owner_only_and_can_be_disabled(tmp_path: Path) -> None:
    with client(tmp_path, where=TUNNEL) as remote:
        assert (
            remote.post("/plans/uploads", files={"file": ("a.wav", b"x", "audio/wav")}).status_code
            == 403
        )
    with client(tmp_path, uploads=False) as c:
        assert (
            c.post("/plans/uploads", files={"file": ("a.wav", b"x", "audio/wav")}).status_code
            == 503
        )


def test_plan_requests_go_to_the_planner(tmp_path: Path) -> None:
    body = {
        "items": [
            {"kind": "text", "text": "quiero pasar la próxima inspección", "lang": "es"},
            {"kind": "voice", "file": "uploads/abc.webm"},
        ]
    }
    with client(tmp_path) as c:
        assert c.post("/plans/requests", json=body).status_code == 503
    planner = FakePlanner()
    with client(tmp_path, planner) as c:
        reply = c.post("/plans/requests", json=body)
        assert c.post("/plans/requests", json={"items": []}).status_code == 422
    assert reply.status_code == 202
    assert reply.json() == {"plan_id": "main-st"}
    assert [i.kind for i in planner.requests[0]] == ["text", "voice"]
    with client(tmp_path, planner, where=TUNNEL) as remote:
        assert remote.post("/plans/requests", json=body).status_code == 403


def test_save_upload_is_idempotent(tmp_path: Path) -> None:
    first = save_upload(tmp_path, b"data", "wav")
    second = save_upload(tmp_path, b"data", "wav")
    assert first == second
    assert first.read_bytes() == b"data"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]
