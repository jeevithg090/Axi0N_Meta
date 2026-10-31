"""Tests for voice disputes and audio disposal (task P2-S2)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.evidence.chain import EventChain, read_chain
from rulecam.worker.api import worker_router
from rulecam.worker.disputes import (
    DisputeService,
    transcribe_and_discard_audio,
)
from rulecam.worker.errors import DisputeLimitError, UnknownEventError
from rulecam.worker.log import event_code
from tests.fakes import EPOCH, FakeClock, FakeTranscriber
from tests.worker.helpers import (
    FakeReviewer,
    World,
    chain_candidate,
    make_candidate,
    make_plan,
    world_with_event,
)


def test_transcribe_and_discard_audio_wipes_mutable_buffer() -> None:
    audio_data = bytearray(b"RIFFWAVEfmt 16khz audio sample of worker dispute")
    transcriber = FakeTranscriber(
        [{"speaker": "A", "text": "I was wearing safety harness", "lang": "en"}]
    )

    text = transcribe_and_discard_audio(audio_data, transcriber)

    assert text == "I was wearing safety harness"
    # The mutable audio buffer must have been wiped with zeros for privacy
    assert audio_data == bytearray(b"\x00" * len(audio_data))


def test_transcribe_and_discard_audio_sanitizes_control_characters() -> None:
    audio = b"fake audio bytes"
    transcriber = FakeTranscriber(
        [{"speaker": "A", "text": "Harness was on\n\x00\x08strap was toolbag", "lang": "en"}]
    )

    text = transcribe_and_discard_audio(audio, transcriber)
    assert text == "Harness was on strap was toolbag"


def test_voice_dispute_submission_chains_with_channel_voice(tmp_path: Path) -> None:
    world, code = world_with_event(tmp_path)
    transcriber = FakeTranscriber(
        [{"speaker": "A", "text": "I had my vest on under my jacket", "lang": "en"}]
    )

    result = world.disputes.submit_voice(
        code,
        b"worker audio clip",
        client="10.0.0.1",
        transcriber=transcriber,
        lang_hint="en",
    )

    assert result.outcome == "upheld"
    assert result.counts.disputes == 1

    # Verify chained records have channel="voice" and no audio bytes
    path = next((tmp_path / "chain" / "main-st").glob("*.jsonl"))
    records = read_chain(path)
    dispute_records = [r for r in records if r.kind == "dispute"]
    assert len(dispute_records) == 2  # pending then reviewed

    first_dispute = dispute_records[0].payload.get("dispute")
    assert isinstance(first_dispute, dict)
    assert first_dispute.get("channel") == "voice"
    assert "I had my vest on under my jacket" in str(first_dispute.get("reason_text"))
    # Verify no audio data was recorded in the payload
    assert "audio" not in first_dispute
    assert "bytes" not in first_dispute


def test_voice_dispute_respects_per_client_and_event_limits(tmp_path: Path) -> None:
    world, code = world_with_event(tmp_path)
    transcriber = FakeTranscriber([{"speaker": "A", "text": "Strap was my radio", "lang": "en"}])

    # 3 disputes per event max
    for _ in range(3):
        world.disputes.submit_voice(
            code,
            b"audio",
            client="10.0.0.1",
            transcriber=transcriber,
        )

    with pytest.raises(DisputeLimitError, match="maximum number of times"):
        world.disputes.submit_voice(
            code,
            b"audio",
            client="10.0.0.1",
            transcriber=transcriber,
        )


def test_voice_dispute_rejects_unknown_event_code(tmp_path: Path) -> None:
    world = World(tmp_path)
    transcriber = FakeTranscriber([{"speaker": "A", "text": "test", "lang": "en"}])

    with pytest.raises(UnknownEventError, match="no recent event"):
        world.disputes.submit_voice(
            "UNKNOWN8",
            b"audio",
            client="10.0.0.1",
            transcriber=transcriber,
        )


def test_api_dispute_voice_endpoint_end_to_end(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path, "main-st-kitchen", clock=clock)
    plan = make_plan()
    chain_candidate(chain, make_candidate("ev-strap-voice", EPOCH - timedelta(minutes=5)))

    reviewer = FakeReviewer()
    disputes = DisputeService(chain, reviewer, clock=clock)
    transcriber = FakeTranscriber(
        [{"speaker": "A", "text": "My lanyard was attached", "lang": "en"}]
    )

    router = worker_router(
        plans=lambda pid: plan if pid == "main-st-kitchen" else None,
        chain=chain,
        disputes=disputes,
        clock=clock,
        public_base_url="https://rules.example.com",
        transcriber=transcriber,
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    code = event_code("ev-strap-voice")
    response = client.post(
        f"/worker/dispute/voice?event_code={code}&lang=en",
        content=b"RIFFWAVEfakeaudiobytes",
        headers={"Content-Type": "audio/wav"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["outcome"] == "upheld"
    assert data["code"] == code

    # Check that without audio body it returns 400
    empty_resp = client.post(
        f"/worker/dispute/voice?event_code={code}",
        content=b"",
    )
    assert empty_resp.status_code == 400


def test_api_dispute_voice_501_when_transcriber_not_configured(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path, "main-st-kitchen", clock=clock)
    plan = make_plan()
    reviewer = FakeReviewer()
    disputes = DisputeService(chain, reviewer, clock=clock)

    router = worker_router(
        plans=lambda pid: plan if pid == "main-st-kitchen" else None,
        chain=chain,
        disputes=disputes,
        clock=clock,
        transcriber=None,  # No transcriber
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.post("/worker/dispute/voice?event_code=ABCDEF12", content=b"audio")
    assert resp.status_code == 501
    assert "not configured" in resp.json()["detail"]
