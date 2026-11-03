"""`/crowd`: watch a camera, read its status, fetch heatmaps and announcements, the live page."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.api.crowd import crowd_router
from rulecam.ask.vision import FrameGrabber
from rulecam.crowd.memory import Calibrations, CrowdMemory
from rulecam.crowd.scan import CrowdScanner
from rulecam.crowd.warden import CrowdWarden
from tests.crowd.conftest import Sam, top_down, wait_for
from tests.fakes import EPOCH, FakeClock, FakeLLM


@pytest.fixture
def parts(tmp_path: Path, grabber: FrameGrabber) -> tuple[CrowdMemory, CrowdWarden]:
    memory = CrowdMemory(
        frames=grabber,
        scanner=CrowdScanner(
            segmenter=Sam([(20.0, 100.0, 40.0, 150.0)]), out_dir=tmp_path / "heat", grid=1
        ),
        calibrations=Calibrations(plan=lambda: {"gate": top_down(0.05)}),
        path=tmp_path / "readings.jsonl",
        every_s=5.0,
    )
    memory.tick()
    wait_for(lambda: memory.latest("gate") is not None)
    warden = CrowdWarden(
        memory=memory,
        frames=grabber,
        spark=FakeLLM([]),
        glimmer=FakeLLM([]),
        glimmer_model="muse-glimmer",
        chain=None,
        notifier=None,
        tts=None,
        clock=FakeClock(EPOCH),
        out_dir=tmp_path / "crowd",
    )
    return memory, warden


def client(memory: CrowdMemory | None, warden: CrowdWarden | None) -> TestClient:
    app = FastAPI()
    app.include_router(crowd_router(memory, warden, camera_names={"gate": "Gate 3"}))
    return TestClient(app)


def test_watch_then_status_shows_the_camera(parts: tuple[CrowdMemory, CrowdWarden]) -> None:
    api = client(*parts)
    made = api.post(
        "/crowd/watch",
        json={"camera_id": "gate", "instruction": "Warn me early.", "languages": ["hi", "en"]},
    )
    assert made.status_code == 200
    assert made.json()["languages"] == ["hi", "en"]
    status = api.get("/crowd/status").json()
    (camera,) = status["cameras"]
    assert camera["name"] == "Gate 3"
    assert camera["watched"]["instruction"] == "Warn me early."
    assert camera["latest"]["people"] == 1
    assert camera["series"]["people"] == [1]
    assert status["alerts"] == []
    assert api.get("/crowd/alerts").json() == []
    assert api.delete("/crowd/watch/gate").json() == {"stopped": True}


def test_files_are_served_by_safe_names_only(parts: tuple[CrowdMemory, CrowdWarden]) -> None:
    memory, warden = parts
    api = client(memory, warden)
    latest = memory.latest("gate")
    assert latest is not None
    assert latest.heatmap is not None
    heat = api.get(f"/crowd/files/{latest.heatmap}")
    assert heat.status_code == 200
    assert heat.headers["content-type"] == "image/jpeg"
    warden.audio_dir.mkdir(parents=True)
    (warden.audio_dir / "crowd-1-0-hi.wav").write_bytes(b"RIFF")
    assert api.get("/crowd/files/crowd-1-0-hi.wav").headers["content-type"] == "audio/wav"
    assert api.get("/crowd/files/..%2Fsecret.jpg").status_code == 404
    assert api.get("/crowd/files/notes.txt").status_code == 404
    assert api.get("/crowd/files/missing.jpg").status_code == 404


def test_the_live_page_is_served(parts: tuple[CrowdMemory, CrowdWarden]) -> None:
    page = client(*parts).get("/crowd/live")
    assert page.status_code == 200
    assert "Crowd Early Warning" in page.text


def test_off_answers_503() -> None:
    api = client(None, None)
    assert api.get("/crowd/status").status_code == 503
    assert api.post("/crowd/watch", json={"camera_id": "g", "instruction": "x"}).status_code == 503
