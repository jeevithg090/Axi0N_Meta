"""The fakes must satisfy the contract Protocols.

mypy checks this file in CI (`make type`): assigning a fake to a Protocol-typed variable fails
type-checking if the fake drifts from the contract. The runtime asserts exercise each fake once.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel

from rulecam.contracts import (
    TTS,
    Clock,
    Notifier,
    Plug,
    RuleIR,
    Segmenter,
    StructuredLLM,
    TrackStore,
    Transcriber,
    Verifier,
)
from tests.fakes import (
    EPOCH,
    FakeClock,
    FakeLLM,
    FakeNotifier,
    FakePlug,
    FakeSegmenter,
    FakeTranscriber,
    FakeTTS,
    FakeVerifier,
    InMemoryTrackStore,
    make_chunk,
    make_track,
)


class _Answer(BaseModel):
    answer: str


def test_fakes_conform_to_protocols() -> None:
    clock: Clock = FakeClock()
    llm: StructuredLLM = FakeLLM([{"answer": "yes"}])
    segmenter: Segmenter = FakeSegmenter()
    store: TrackStore = InMemoryTrackStore()
    verifier: Verifier = FakeVerifier()
    tts: TTS = FakeTTS()
    transcriber: Transcriber = FakeTranscriber([{"speaker": "A", "text": "hola", "lang": "es"}])
    notifier: Notifier = FakeNotifier()
    plug: Plug = FakePlug()

    assert clock.now() == EPOCH
    assert llm.structured(model="m", messages=[], schema=_Answer).answer == "yes"
    assert segmenter.segment(make_chunk(), "person") == []
    store.append([make_track(feet_m=[(0, 0), (1, 0)])])
    assert len(store.window("cam1", EPOCH, EPOCH + timedelta(seconds=1))) == 1
    assert tts.synth("hola", "es").startswith(b"RIFF")
    assert transcriber.transcribe(b"x")[0]["text"] == "hola"
    assert notifier.send("aide", "t", "b") == "msg-1"
    plug.off()
    assert verifier is not None


def test_fake_llm_validates_and_records_calls() -> None:
    llm = FakeLLM([{"answer": "no"}])
    result = llm.structured(model="muse-spark-1.3", messages=[], schema=_Answer)
    assert result.answer == "no"
    assert llm.calls[0]["schema"] == "_Answer"


def test_fake_segmenter_records_backward_direction() -> None:
    segmenter = FakeSegmenter()
    segmenter.segment(
        make_chunk(), "harness", negative_points=[(5, 1.0, 2.0)], direction="backward"
    )
    assert segmenter.calls[0].direction == "backward"
    assert segmenter.calls[0].negative_points == [(5, 1.0, 2.0)]


def test_in_memory_store_filters_by_time_and_camera() -> None:
    store = InMemoryTrackStore()
    store.append([make_track(feet_m=[(0, 0)] * 20), make_track(camera_id="cam2")])
    window = store.window("cam1", EPOCH, EPOCH + timedelta(seconds=0.45))
    assert len(window) == 1
    assert len(window[0].points) == 5


def test_rule_ir_is_importable_for_verifier_fakes() -> None:
    assert RuleIR.__name__ == "RuleIR"
