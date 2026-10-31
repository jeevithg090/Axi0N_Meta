"""Unit tests for the hosted Voice Transcribe client (task P2-01). No network."""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ClientConfigError, ModelResponseError
from rulecam.clients.meta_hosted.voice import (
    StreamingVoiceSession,
    VoiceTranscribeClient,
    language_names,
    parse_transcript,
    split_wav,
)
from rulecam.clients.relay import ModelUsed
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache
from rulecam.replay.errors import ReplayMiss
from tests.clients.meta_hosted.transport_fakes import ScriptedTransport
from tests.fakes import FakeClock


class RecordingBus(Bus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[Topic, object]] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append((topic, payload))
        super().publish(topic, payload)


def wav_bytes(duration_s: float, rate: int = 16_000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * channels * int(duration_s * rate))
    return buf.getvalue()


def transcript_reply(turns: list[dict[str, Any]], transcript: str = "") -> dict[str, Any]:
    """A reply in the documented `/asr/transcribe` JSON shape."""
    return {
        "status": 200,
        "headers": {},
        "json": {
            "sessionId": "sess_1",
            "transcript": transcript or " ".join(t["transcript"] for t in turns),
            "audioDurationMs": 4000,
            "turns": turns,
        },
    }


def _turn(i: int, text: str, speaker: str | None = None) -> dict[str, Any]:
    turn: dict[str, Any] = {"turnId": i, "startMs": i * 1000, "endMs": i * 1000 + 900}
    turn["transcript"] = text
    if speaker is not None:
        turn["speaker"] = speaker
    return turn


def test_diarized_transcription_sends_documented_multipart() -> None:
    transport = ScriptedTransport(
        transcript_reply(
            [_turn(0, "Lávate las manos.", "SPEAKER_0"), _turn(1, "Use the sink.", "SPEAKER_1")]
        )
    )
    bus = RecordingBus()
    client = VoiceTranscribeClient(transport, clock=FakeClock(), bus=bus)

    segments = client.transcribe(
        wav_bytes(1.0), lang_bias=["es", "en"], keywords=["sanitizer"], diarize=True
    )

    assert segments == [
        {"speaker": "SPEAKER_0", "text": "Lávate las manos.", "lang": "und"},
        {"speaker": "SPEAKER_1", "text": "Use the sink.", "lang": "und"},
    ]
    [request] = transport.requests
    assert (request["method"], request["path"]) == ("POST", "/asr/transcribe")
    parts = request["parts"]
    assert isinstance(parts, dict)
    assert parts["request"]["content_type"] == "application/json"
    assert json.loads(parts["request"]["content"]) == {
        "model": "muse-voice-transcribe-1.0",
        "audioEncoding": "WAV",
        "mode": "DIARIZATION",
        "languageBias": ["Spanish", "English"],
        "keywords": ["sanitizer"],
    }
    assert parts["audio"]["content_type"] == "audio/wav"
    [(_, event)] = bus.published
    assert isinstance(event, ModelUsed)
    assert event.model == "muse-voice-transcribe-1.0"


def test_push_to_talk_uses_the_whole_transcript_and_single_bias_lang() -> None:
    transport = ScriptedTransport(transcript_reply([], transcript="Wash your hands"))
    client = VoiceTranscribeClient(transport, clock=FakeClock())

    segments = client.transcribe(wav_bytes(0.5), lang_bias=["en"])

    assert segments == [{"speaker": "speaker_0", "text": "Wash your hands", "lang": "en"}]
    settings = json.loads(transport.requests[0]["parts"]["request"]["content"])  # type: ignore[index]
    assert settings["mode"] == "PUSH_TO_TALK"


def test_splitting_files_over_10_minutes() -> None:
    audio = wav_bytes(650.0, rate=16_000)
    chunks = split_wav(audio)
    assert len(chunks) == 2
    durations = []
    for chunk in chunks:
        with wave.open(io.BytesIO(chunk), "rb") as reader:
            durations.append(reader.getnframes() / reader.getframerate())
    assert durations == [600.0, 50.0]

    transport = ScriptedTransport(
        transcript_reply([_turn(0, "first", "A")]), transcript_reply([_turn(0, "second", "A")])
    )
    segments = VoiceTranscribeClient(transport, clock=FakeClock()).transcribe(audio, diarize=True)
    assert [s["speaker"] for s in segments] == ["c0_A", "c1_A"]
    assert len(transport.requests) == 2


def test_split_respects_the_byte_limit() -> None:
    chunks = split_wav(wav_bytes(10.0), max_bytes=200_000)
    assert len(chunks) > 1
    assert all(len(c) <= 200_000 for c in chunks)


def test_rejects_audio_the_api_does_not_take() -> None:
    client = VoiceTranscribeClient(ScriptedTransport(), clock=FakeClock())
    with pytest.raises(ValueError, match="RIFF/WAVE"):
        client.transcribe(b"ID3 not a wav")
    with pytest.raises(ValueError, match="mono 16-bit"):
        client.transcribe(wav_bytes(0.1, rate=8_000))
    with pytest.raises(ValueError, match="mono 16-bit"):
        client.transcribe(wav_bytes(0.1, channels=2))


def test_parse_transcript_rejects_unknown_shapes() -> None:
    with pytest.raises(ModelResponseError):
        parse_transcript({"text": "x"}, lang="en")
    with pytest.raises(ModelResponseError):
        parse_transcript({"transcript": "x"}, lang="en")
    assert parse_transcript({"transcript": "", "turns": []}, lang="en") == []


def test_language_names_map_tags_and_pass_names_through() -> None:
    assert language_names(["en-US", "es", "English", "Klingon"]) == [
        "English",
        "Spanish",
        "Klingon",
    ]
    assert language_names(None) == []


def test_streaming_session_keywords_only_at_session_start() -> None:
    client = VoiceTranscribeClient(ScriptedTransport(), clock=FakeClock())
    session = client.start_streaming_session(
        "DIARIZATION", api_key="k", keywords=["excavator"], lang_bias=["es"]
    )
    assert isinstance(session, StreamingVoiceSession)

    assert json.loads(session.handshake()) == {
        "authorization": {"accessToken": "Bearer k"},
        "audioEncoding": "PCM_24KHZ",
        "model": "muse-voice-transcribe-1.0",
        "mode": "DIARIZATION",
        "languageBias": ["Spanish"],
        "keywords": ["excavator"],
    }
    with pytest.raises(ValueError, match="session start"):
        session.set_keywords(["forklift"])
    with pytest.raises(ValueError, match="session start"):
        session.set_language_bias(["en"])
    assert session.keywords == ("excavator",)


def test_replay_miss_raises_replay_miss(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = ReplayCache(tmp_path, "replay", clock=clock)
    inner = ScriptedTransport()
    client = VoiceTranscribeClient(
        cache.wrap_transport("meta", "muse-voice-transcribe-1.0", inner), clock=clock
    )
    with pytest.raises(ReplayMiss):
        client.transcribe(wav_bytes(0.2))
    assert inner.requests == []


def test_from_settings(tmp_path: Path) -> None:
    replay = Settings(rulecam_mode="replay", rulecam_replay_dir=tmp_path)
    with pytest.raises(ReplayMiss):
        VoiceTranscribeClient.from_settings(replay, clock=FakeClock()).transcribe(wav_bytes(0.1))
    live = Settings(
        rulecam_mode="live",
        meta_api_base_url="https://api.meta.ai/v1",
        meta_api_key=SecretStr("k"),
        rulecam_replay_dir=tmp_path,
    )
    assert isinstance(
        VoiceTranscribeClient.from_settings(live, clock=FakeClock()), VoiceTranscribeClient
    )
    with pytest.raises(ClientConfigError):
        VoiceTranscribeClient.from_settings(
            Settings(rulecam_mode="live", meta_api_base_url=None, meta_api_key=None),
            clock=FakeClock(),
        )
