"""`/crowd`: set up and watch a camera, its status and stream, acks, venue, truth and report."""

from __future__ import annotations

import json
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
    assert camera["zone_series"]["view"]["people"] == [1]
    assert camera["forecast"][0]["zone"] == "view"
    assert camera["latest"]["cells"]
    assert status["alerts"] == []
    assert status["acks"] == {}
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


def test_the_stream_sends_the_status_as_server_sent_events(
    parts: tuple[CrowdMemory, CrowdWarden],
) -> None:
    answer = client(*parts).get("/crowd/stream?limit=1")
    assert answer.headers["content-type"].startswith("text/event-stream")
    event, data = answer.text.strip().split("\n", 1)
    assert event == "event: status"
    assert json.loads(data.removeprefix("data: "))["cameras"][0]["camera_id"] == "gate"


def test_venue_truth_report_ack_and_setup_routes(parts: tuple[CrowdMemory, CrowdWarden]) -> None:
    api = client(*parts)
    venue = {"description": "SAMPLE", "places": [{"name": "Gate A", "camera_id": "gate"}]}
    assert api.put("/crowd/venue", json=venue).json()["places"][0]["name"] == "Gate A"
    assert api.get("/crowd/venue").json()["description"] == "SAMPLE"
    places = api.get("/crowd/status").json()["places"]
    assert places[0]["now"]["people"] == 1
    truth = {"source": "SAMPLE", "zones": {"view": {"t": [0, 1], "people": [1, 1]}}}
    assert api.put("/crowd/truth/gate", json=truth).json()["zones"] == ["view"]
    assert api.get("/crowd/truth/gate").json()["source"] == "SAMPLE"
    assert api.get("/crowd/truth/other").status_code == 404
    assert api.put("/crowd/truth/gate", json={"zones": 1}).status_code == 422
    report = api.get("/crowd/report", params={"camera_id": "gate"}).json()
    assert report["readings"] == 1
    assert report["truth_source"] == "SAMPLE"
    assert api.post("/crowd/alerts/nope/ack", json={"role": "stewards"}).status_code == 404
    setup = api.post("/crowd/setup", json={"camera_id": "gate", "instruction": "Watch the gate"})
    assert setup.status_code == 422
    assert "images" in setup.text


def test_off_answers_503() -> None:
    api = client(None, None)
    assert api.get("/crowd/status").status_code == 503
    assert api.post("/crowd/watch", json={"camera_id": "g", "instruction": "x"}).status_code == 503


def test_bad_floor_points_answer_422(parts: tuple[CrowdMemory, CrowdWarden]) -> None:
    bad = {
        "image_px": [[0, 0], [1, 1], [2, 2], [3, 3]],
        "ground_m": [[0, 0], [1, 1], [2, 2], [3, 3]],
    }
    answer = client(*parts).post(
        "/crowd/watch", json={"camera_id": "gate", "instruction": "x", "floor": bad}
    )
    assert answer.status_code == 422
    assert "floor points" in answer.text
