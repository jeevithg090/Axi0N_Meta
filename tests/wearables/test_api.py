"""Tests for the `/wearables` routes."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

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
    if assistant is not None:
        assistant.mark_glimmer(up=True)  # Glimmer looks first; Spark only composes
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
    api, glimmer, _ = client(tmp_path)
    glimmer.queue({"ok": True})
    with api:
        body = api.get("/wearables/status").json()
        assert body["glimmer"] == "up"  # an up Glimmer stays up while it is pinged again
        for _ in range(100):
            if not glimmer.calls:
                time.sleep(0.02)
        assert [c["schema"] for c in glimmer.calls] == ["GlimmerPing"]
    assert body["ready"] is True
    assert body["look"] == "muse-glimmer-30b"
    assert body["voice"] == "test voice"
    assert body["transcribe"] is None
    assert body["modes"] == ["aisle", "warehouse", "crowd", "general"]


def test_without_spark_looks_answer_503(tmp_path: Path) -> None:
    api, _, _ = client(tmp_path, ready=False)
    status = api.get("/wearables/status").json()
    assert status["ready"] is False
    assert status["glimmer"] == "none"
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


def poll(api: TestClient, job: dict[str, Any]) -> dict[str, Any]:
    for _ in range(200):
        state: dict[str, Any] = api.get(f"/wearables/jobs/{job['job_id']}").json()
        if state["status"] != "running":
            return state
        time.sleep(0.02)
    raise AssertionError("job still running")


def test_background_jobs(tmp_path: Path) -> None:
    api, glimmer, spark = client(tmp_path)
    glimmer.queue(look())
    spark.queue(composed(alert=False, spoken="All clear."))
    with api:
        started = api.post(
            "/wearables/look",
            files={"image": ("f.jpg", jpeg(), "image/jpeg")},
            data={"question": "Clear?", "wait": "false"},
        ).json()
        assert started["status"] == "running"
        done = poll(api, started)
        assert done["status"] == "done"
        assert done["answer"]["spoken"] == "All clear."
        bad = api.post(
            "/wearables/look",
            files={"image": ("f.gif", b"GIF89a", "image/gif")},
            data={"wait": "false"},
        ).json()
        failed = poll(api, bad)
        assert failed["status"] == "failed"
        assert "JPEG or PNG" in failed["error"]
        walk = api.post("/wearables/walks", json={"mode": "aisle"}).json()
        finish = api.post(f"/wearables/walks/{walk['walk_id']}/finish?wait=false").json()
        report = poll(api, finish)["report"]
        assert report["summary"]["summary"] == "No frames were sent during this walk."
        assert api.get("/wearables/jobs/nope").status_code == 404


class FinishingGlimmer:
    """Glimmer that finishes the walk while it looks (the wearer tapped Finish meanwhile)."""

    def __init__(self, walks: WalkStore) -> None:
        self.walks = walks
        self.walk_id = ""

    def structured(
        self, *, model: str, messages: list[dict[str, object]], schema: Any, **_: object
    ) -> Any:
        self.walks.finish(self.walk_id)
        return schema.model_validate(look())


def test_a_walk_finished_mid_look_keeps_the_answer(tmp_path: Path) -> None:
    clock = FakeClock()
    walks = WalkStore(tmp_path / "walks", clock=clock)
    glimmer = FinishingGlimmer(walks)
    assistant = PovAssistant(
        spark=FakeLLM(),
        files=Files(),
        sam=Sam({}),
        out_dir=tmp_path / "wearables",
        clock=clock,
        glimmer=glimmer,
    )
    app = FastAPI()
    app.include_router(wearables_router(assistant, walks, None, glimmer_timeout_s=25.0))
    api = TestClient(app)
    walk = api.post("/wearables/walks", json={"mode": "warehouse"}).json()
    glimmer.walk_id = walk["walk_id"]
    reply = api.post(
        "/wearables/look",
        files={"image": ("f.jpg", jpeg(), "image/jpeg")},
        data={"walk_id": walk["walk_id"], "mode": "warehouse"},
    )
    assert reply.status_code == 200
    assert reply.json()["kind"] == "patrol"
    assert api.get(f"/wearables/walks/{walk['walk_id']}").json()["walk"]["frames"] == 0
