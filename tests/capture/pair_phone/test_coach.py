"""P1-09 placement coach: bars from a fake single-frame segmenter over the WebSocket."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from rulecam.capture.pair_phone import PairPhone, concept_confidence
from rulecam.capture.pair_phone.coach import HINT_MAX_CHARS, clean_hint, measure_bars
from rulecam.capture.pair_phone.errors import PairingError
from rulecam.contracts import Detection, Mask
from tests.capture.pair_phone.conftest import FakeFrameSegmenter, Phone
from tests.fakes import EPOCH


def detection(confidence: float | None) -> Detection:
    mask = Mask(rle="x", box_xyxy=(0, 0, 1, 1), confidence=confidence)
    return Detection(camera_id="c", frame_idx=0, ts=EPOCH, concept="sink", object_id=1, mask=mask)


def coach_url(phone: Phone) -> str:
    return f"wss://testserver/pair/{phone.token}/coach"


def test_concept_confidence_is_the_best_detection_and_zero_when_nothing_matches() -> None:
    assert concept_confidence([]) == 0.0
    assert concept_confidence([detection(0.3), detection(0.8)]) == 0.8
    assert concept_confidence([detection(None)]) == 1.0


async def test_measure_bars_asks_one_concept_per_request(tmp_path: Path) -> None:
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")
    segmenter = FakeFrameSegmenter({"sink": 0.9123})

    bars = await measure_bars(segmenter, frame, ("sink", "soap"))

    assert bars == {"sink": 0.912, "soap": 0.0}
    assert sorted(concept for _, concept in segmenter.calls) == ["sink", "soap"]


def test_clean_hint_is_one_short_line() -> None:
    assert clean_hint("Tilt down\n  to see\tthe sink") == "Tilt down to see the sink"
    assert len(clean_hint("x" * 500)) == HINT_MAX_CHARS


def test_coach_streams_bars_from_the_segmenter(
    phone: Phone, segmenter: FakeFrameSegmenter, webm_clip: bytes
) -> None:
    assert phone.upload(webm_clip).status_code == 201

    with phone.client.websocket_connect(coach_url(phone)) as socket:
        socket.send_json({"session": phone.session})
        hello = socket.receive_json()
        first = socket.receive_json()
        second = socket.receive_json()

    assert hello == {
        "type": "hello",
        "camera_id": "phone-1",
        "concepts": ["sink", "soap dispenser", "paper towels"],
    }
    expected = {"sink": 0.91, "soap dispenser": 0.42, "paper towels": 0.0}
    assert first == {
        "type": "bars",
        "camera_id": "phone-1",
        "chunk_id": "phone-1_000000",
        "bars": expected,
    }
    assert second == first  # streamed again on the next tick ...
    assert len(segmenter.calls) == 3  # ... but each chunk is segmented only once


def test_coach_reports_waiting_before_the_first_chunk(phone: Phone) -> None:
    with phone.client.websocket_connect(coach_url(phone)) as socket:
        socket.send_json({"session": phone.session})
        socket.receive_json()
        status = socket.receive_json()

    assert status == {"type": "status", "text": "Waiting for the first video chunk"}


def test_coach_relays_a_planner_hint(phone: Phone, service: PairPhone) -> None:
    service.push_hint(phone.camera_id, "Raise the phone\nto see the whole sink")

    with phone.client.websocket_connect(coach_url(phone)) as socket:
        socket.send_json({"session": phone.session})
        messages = [socket.receive_json() for _ in range(3)]

    assert {"type": "hint", "text": "Raise the phone to see the whole sink"} in messages


def test_coach_survives_a_segmenter_failure(
    phone: Phone, segmenter: FakeFrameSegmenter, webm_clip: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(frame: Path, concept: str) -> list[Detection]:
        raise PairingError("SAM is down")

    monkeypatch.setattr(segmenter, "segment_image", broken)
    assert phone.upload(webm_clip).status_code == 201

    with phone.client.websocket_connect(coach_url(phone)) as socket:
        socket.send_json({"session": phone.session})
        socket.receive_json()
        status = socket.receive_json()

    assert status == {"type": "status", "text": "Coach unavailable, retrying"}


@pytest.mark.parametrize("login", [{"session": "wrong"}, {"nope": 1}, ["session"]])
def test_coach_rejects_a_bad_session(phone: Phone, login: object) -> None:
    with phone.client.websocket_connect(coach_url(phone)) as socket:
        socket.send_json(login)
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()

    assert closed.value.code == 1008


def test_coach_rejects_plain_ws_off_localhost(
    phone: Phone, make_client: Callable[..., TestClient]
) -> None:
    lan = make_client(base_url="http://192.168.1.20:8000")
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        lan.websocket_connect(f"ws://192.168.1.20:8000/pair/{phone.token}/coach"),
    ):
        pass

    assert closed.value.code == 1008


def test_coach_login_times_out(phone: Phone, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rulecam.capture.pair_phone.server.COACH_LOGIN_TIMEOUT_S", 0.05)
    with (
        phone.client.websocket_connect(coach_url(phone)) as socket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        socket.receive_json()

    assert closed.value.code == 1008


def test_coach_stops_when_the_phone_disconnects(phone: Phone) -> None:
    with phone.client.websocket_connect(coach_url(phone)) as socket:
        socket.send_json({"session": phone.session})
        assert socket.receive_json()["type"] == "hello"
        assert socket.receive_json()["type"] == "status"
    # Leaving the block disconnects and waits for the handler: a coach loop that ignored the
    # disconnect would hang here.
