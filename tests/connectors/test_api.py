"""`/connectors` end to end on a rendered clip: add, health, snapshot, survey, remove, secrets."""

from __future__ import annotations

import stat
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.bus import Bus
from rulecam.clock import SystemClock
from rulecam.connectors import api as connectors_api
from rulecam.connectors.discovery import OnvifDevice, OnvifProfile
from rulecam.connectors.errors import DiscoveryError
from rulecam.connectors.manager import ConnectorManager, ConnectorSettings
from rulecam.connectors.sources import ResolvedInput, SourceKind, input_args
from rulecam.connectors.survey import CameraSurveyor
from tests.connectors.test_survey import FIT, LOOK, Attacher, ImageSam
from tests.fakes import FakeLLM


def wait_for(check: Callable[[], bool], timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise AssertionError("condition not met in time")


@pytest.fixture
def setup(tmp_path: Path, clip: Path) -> Iterator[tuple[TestClient, ConnectorManager, Path]]:
    clips = tmp_path / "clips"
    clips.mkdir()
    local = clips / "shop.mp4"
    local.write_bytes(clip.read_bytes())
    surveyor = CameraSurveyor(
        spark=FakeLLM([LOOK, FIT]),
        files=Attacher(),
        sam=ImageSam({"person": 2}),
        clock=SystemClock(),
        out_dir=tmp_path / "connectors" / "surveys",
    )

    def fake_public(url: str) -> ResolvedInput:
        return ResolvedInput(args=input_args(SourceKind.FILE, str(local)), title="Town square")

    manager = ConnectorManager(
        ConnectorSettings(root=tmp_path / "connectors", chunk_seconds=1.0, clip_roots=(clips,)),
        bus=Bus(),
        clock=SystemClock(),
        surveyor=surveyor,
        resolve_public_url=fake_public,
    )
    app = FastAPI()
    app.include_router(connectors_api.connectors_router(manager))
    with TestClient(app) as client:
        yield client, manager, local


def test_add_a_clip_by_path_runs_and_is_surveyed(
    setup: tuple[TestClient, ConnectorManager, Path],
) -> None:
    client, manager, local = setup
    reply = client.post("/connectors/cameras", json={"url": str(local)})
    assert reply.status_code == 201
    camera = reply.json()
    assert camera["camera_id"] == "cam-1"
    assert camera["kind"] == "file"
    assert camera["provenance"] == "upload"
    assert camera["display_url"] == "shop.mp4"
    wait_for(lambda: (manager.survey("cam-1") or LOOK).model_dump().get("status") == "done")
    camera = client.get("/connectors/cameras/cam-1").json()
    assert camera["health"]["state"] == "live"
    assert camera["health"]["chunks"] >= 1
    assert camera["name"] == "Checkout lanes 1-3"
    assert camera["named_by"] == "spark"
    survey = client.get("/connectors/survey/cam-1").json()
    assert survey["status"] == "done"
    assert survey["detections"][0]["count"] == 2
    assert "first_frame_s" in survey["timings"]
    shot = client.get("/connectors/snapshot/cam-1.jpg")
    assert shot.status_code == 200
    assert shot.headers["content-type"] == "image/jpeg"
    assert client.get(f"/connectors/images/{survey['snapshot']}").status_code == 200
    assert client.get(f"/connectors/images/{survey['marked']}").status_code == 200
    assert client.get("/connectors/images/..%2Fcameras.json").status_code == 404
    assert client.delete("/connectors/cameras/cam-1").status_code == 204
    assert client.get("/connectors/cameras").json() == []
    assert client.get("/connectors/cameras/cam-1").status_code == 404


def test_credentials_stay_server_side(
    setup: tuple[TestClient, ConnectorManager, Path],
) -> None:
    client, manager, _ = setup
    reply = client.post(
        "/connectors/cameras",
        json={
            "url": "rtsp://admin:Hunter-22@127.0.0.1:1/Streaming/Channels/101",
            "survey": False,
        },
    )
    assert reply.status_code == 201
    body = reply.text
    assert "Hunter-22" not in body
    assert reply.json()["has_credentials"] is True
    assert reply.json()["display_url"] == "rtsp://127.0.0.1:1/Streaming/Channels/101"
    wait_for(lambda: manager.camera("cam-1").health.errors >= 1)
    listing = client.get("/connectors/cameras").text
    assert "Hunter-22" not in listing
    secrets = manager.root / "secrets.json"
    assert "Hunter-22" in secrets.read_text()
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert "Hunter-22" not in (manager.root / "cameras.json").read_text()
    client.delete("/connectors/cameras/cam-1")
    assert "Hunter-22" not in secrets.read_text()


def test_public_stream_and_upload(setup: tuple[TestClient, ConnectorManager, Path]) -> None:
    client, manager, local = setup
    reply = client.post(
        "/connectors/cameras",
        json={"url": "https://www.youtube.com/watch?v=x", "name": "Square", "survey": False},
    )
    assert reply.json()["kind"] == "public"
    assert reply.json()["provenance"] == "public"
    wait_for(lambda: manager.camera("cam-1").health.title == "Town square")
    with local.open("rb") as handle:
        uploaded = client.post(
            "/connectors/cameras/upload",
            files={"clip": ("shop.mp4", handle, "video/mp4")},
            data={"name": "Till"},
        )
    assert uploaded.status_code == 201
    assert uploaded.json()["name"] == "Till"
    bad = client.post(
        "/connectors/cameras/upload", files={"clip": ("x.txt", b"hello", "text/plain")}
    )
    assert bad.status_code == 415


def test_bad_sources_and_unknown_cameras(
    setup: tuple[TestClient, ConnectorManager, Path],
) -> None:
    client, _, _ = setup
    assert client.post("/connectors/cameras", json={"url": "ftp://x/y"}).status_code == 422
    assert client.post("/connectors/cameras", json={"url": "/etc/passwd"}).status_code == 422
    assert client.post("/connectors/cameras/cam-9/restart").status_code == 404
    assert client.delete("/connectors/cameras/cam-9").status_code == 404
    assert client.get("/connectors/snapshot/cam-9.jpg").status_code == 404
    assert client.get("/connectors/survey/cam-9").status_code == 404


def test_restart_and_restore(setup: tuple[TestClient, ConnectorManager, Path]) -> None:
    client, manager, local = setup
    client.post("/connectors/cameras", json={"url": str(local), "survey": False})
    wait_for(lambda: manager.camera("cam-1").health.chunks >= 1)
    assert client.post("/connectors/cameras/cam-1/restart").status_code == 200
    manager.close()
    again = ConnectorManager(
        ConnectorSettings(root=manager.root, chunk_seconds=1.0, clip_roots=(local.parent,)),
        bus=Bus(),
        clock=SystemClock(),
    )
    assert again.camera("cam-1").health.state == "stopped"
    again.tick()
    wait_for(lambda: again.camera("cam-1").health.chunks >= 1)
    assert again.start_survey("cam-1").status == "unavailable"
    again.close()


def test_discovery_routes(
    setup: tuple[TestClient, ConnectorManager, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = setup
    device = OnvifDevice(
        xaddr="http://10.0.0.5/onvif/device_service",
        profiles=[OnvifProfile(token="p1", name="main", rtsp_url="rtsp://10.0.0.5/1")],  # noqa: S106
    )
    seen: list[tuple[str, str | None, str | None]] = []

    def fake_onvif(target: str, user: str | None, password: str | None) -> OnvifDevice:
        seen.append((target, user, password))
        if password == "bad":  # noqa: S105
            msg = "the camera refused the user name or password"
            raise DiscoveryError(msg)
        return device

    monkeypatch.setattr(connectors_api, "onvif_streams", fake_onvif)
    ok = client.post(
        "/connectors/onvif", json={"target": "10.0.0.5", "username": "a", "password": "b"}
    )
    assert ok.json()["profiles"][0]["rtsp_url"] == "rtsp://10.0.0.5/1"
    assert seen[0] == ("10.0.0.5", "a", "b")
    refused = client.post(
        "/connectors/onvif", json={"target": "10.0.0.5", "username": "a", "password": "bad"}
    )
    assert refused.status_code == 502
    brands = client.get("/connectors/brands").json()
    assert any(b["brand"] == "Hikvision" for b in brands)


def test_damaged_store_does_not_stop_the_api(tmp_path: Path) -> None:
    root = tmp_path / "connectors"
    root.mkdir()
    (root / "cameras.json").write_text("{not json")
    manager = ConnectorManager(ConnectorSettings(root=root), bus=Bus(), clock=SystemClock())
    assert manager.cameras() == []
    assert (root / "cameras.unreadable").is_file()
