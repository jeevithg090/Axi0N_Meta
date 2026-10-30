"""`POST /correction` on a bare FastAPI app (P3-08 mounts the router later)."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.bus import Topic
from rulecam.contracts import CandidateEvent
from rulecam.perception.correction_api import correction_router
from tests.fakes import FakeSegmenter
from tests.perception.correction_helpers import STRAP_CENTRE, Harness, build, strap_detections


@dataclass
class RecordingBus:
    published: list[tuple[Topic, object]] = field(default_factory=list)

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append((topic, payload))


def client(h: Harness, bus: RecordingBus) -> TestClient:
    app = FastAPI()
    app.include_router(correction_router(h.service, bus))
    return TestClient(app)


def body(h: Harness, frame_idx: int = 59) -> dict[str, object]:
    candidate = h.record.payload["candidate"]
    assert isinstance(candidate, dict)
    return {
        "event_id": candidate["event_id"],
        "frame_idx": frame_idx,
        "x": STRAP_CENTRE[0],
        "y": STRAP_CENTRE[1],
    }


def test_post_correction_cancels_and_publishes() -> None:
    h, bus = build(), RecordingBus()
    response = client(h, bus).post("/correction", json=body(h))
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "cancelled"
    assert data["label"]["reason"] == "segmentation"
    assert data["candidate"]["predicate_trace"]["status"] == "cancelled"
    assert len(bus.published) == 1
    topic, payload = bus.published[0]
    assert topic is Topic.CANDIDATE_NEW
    assert isinstance(payload, CandidateEvent)
    assert payload.event_id == data["event_id"]
    assert {c.direction for c in h.segmenter.calls} == {"backward"}


def test_still_firing_publishes_nothing() -> None:
    h, bus = build(FakeSegmenter(responder=strap_detections)), RecordingBus()
    response = client(h, bus).post("/correction", json=body(h))
    assert response.status_code == 200
    assert response.json()["status"] == "still_firing"
    assert bus.published == []


def test_errors_map_to_http_codes() -> None:
    h, bus = build(), RecordingBus()
    http = client(h, bus)
    assert http.post("/correction", json={**body(h), "event_id": "ev-nope"}).status_code == 404
    assert http.post("/correction", json=body(h, frame_idx=500)).status_code == 422
    assert http.post("/correction", json=body(h, frame_idx=-1)).status_code == 422
    assert http.post("/correction", json={**body(h), "x": 999.0}).status_code == 422
    assert bus.published == []
