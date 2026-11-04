"""Tests for the `/wearables` routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.api.wearables import wearables_router
from rulecam.evidence.chain import EventChain
from rulecam.wearables.assist import PovAssistant, Voice
from rulecam.wearables.walks import WalkReporter, WalkStore
from tests.fakes import FakeClock, FakeLLM, FakeTTS
from tests.wearables.test_assist import Files, Sam, composed, jpeg, look


def client(tmp_path: Path, *, ready: bool = True) -> tuple[TestClient, FakeLLM, FakeLLM]:
    clock = FakeClock()
    glimmer, spark = FakeLLM(), FakeLLM()
    files = Files()
    assistant = (
        PovAssistant(
            spark=spark,
            files=files,
            sam=Sam({"forklift": 1}),
            out_dir=tmp_path / "wearables",
            clock=clock,
            glimmer=glimmer,
            voice=Voice(FakeTTS(), "test voice"),
        )
        if ready
        else None
    )
    reporter = WalkReporter(
        spark=spark,
        files=files,
        chain=EventChain(tmp_path / "chain", "demo", clock=clock),
        frame_path=lambda n: tmp_path / "wearables" / "frames" / n,
    )
    app = FastAPI()
    app.include_router(
        wearables_router(
            assistant,
            WalkStore(tmp_path / "walks", clock=clock),
            reporter if ready else None,
            glimmer_timeout_s=25.0,
        )
    )
    return TestClient(app), glimmer, spark


def test_status(tmp_path: Path) -> None:
    api, _, _ = client(tmp_path)
    body = api.get("/wearables/status").json()
    assert body["ready"] is True
    assert body["look"] == "muse-glimmer-30b"
    assert body["voice"] == "test voice"
    assert body["transcribe"] is None
    assert body["modes"] == ["aisle", "warehouse", "crowd", "general"]


def test_without_spark_looks_answer_503(tmp_path: Path) -> None:
    api, _, _ = client(tmp_path, ready=False)
    assert api.get("/wearables/status").json()["ready"] is False
    reply = api.post("/wearables/look", files={"image": ("f.jpg", jpeg(), "image/jpeg")})
    assert reply.status_code == 503


def test_look_and_files(tmp_path: Path) -> None:
    api, glimmer, spark = client(tmp_path)
    glimmer.queue(look(alert=True, hazards=["forklift"], count=["forklift"]))
    spark.queue(composed())
    reply = api.post(
        "/wearables/look",
        files={"image": ("f.jpg", jpeg(), "image/jpeg")},
        data={"question": "Safe?", "mode": "warehouse", "source": "mock-device"},
    )
    assert reply.status_code == 200
    body = reply.json()
    assert body["verified"] is True
    assert body["source"] == "mock-device"
    assert api.get(f"/wearables/frames/{body['frame']}").status_code == 200
    assert api.get(f"/wearables/frames/{body['marked']}").status_code == 200
    audio = api.get(f"/wearables/audio/{body['audio']}")
    assert audio.headers["content-type"] == "audio/wav"
    assert api.get("/wearables/frames/..%2Fsecret.jpg").status_code == 404
    assert api.get("/wearables/audio/nope.wav").status_code == 404


def test_bad_image_type(tmp_path: Path) -> None:
    api, _, _ = client(tmp_path)
    reply = api.post("/wearables/look", files={"image": ("f.gif", b"GIF89a", "image/gif")})
    assert reply.status_code == 422


def test_walk_lifecycle(tmp_path: Path) -> None:
    api, glimmer, spark = client(tmp_path)
    walk = api.post("/wearables/walks", json={"mode": "warehouse", "note": "dock"}).json()
    glimmer.queue(look())
    reply = api.post(
        "/wearables/look",
        files={"image": ("f.jpg", jpeg(), "image/jpeg")},
        data={"walk_id": walk["walk_id"], "mode": "warehouse"},
    )
    assert reply.json()["walk_id"] == walk["walk_id"]
    view = api.get(f"/wearables/walks/{walk['walk_id']}").json()
    assert view["walk"]["frames"] == 1
    assert view["report"] is None
    spark.queue(
        {"summary": "Quiet walk.", "spoken": "Nothing found.", "hazards": [], "restock": []}
    )
    report = api.post(f"/wearables/walks/{walk['walk_id']}/finish").json()
    assert report["summary"]["spoken"] == "Nothing found."
    assert len(report["chain_hashes"]) == 1
    again = api.post(f"/wearables/walks/{walk['walk_id']}/finish").json()
    assert again == report
    late = api.post(
        "/wearables/look",
        files={"image": ("f.jpg", jpeg(), "image/jpeg")},
        data={"walk_id": walk["walk_id"]},
    )
    assert late.status_code == 409
    assert api.get("/wearables/walks/walk-missing-000000").status_code == 404
