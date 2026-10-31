"""Unit tests for Muse Voice Transcribe client (`VoiceTranscribeClient`) (task P2-01)."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest
from pydantic import SecretStr

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted.voice import (
    StreamingVoiceSession,
    VoiceTranscribeClient,
    split_wav_audio,
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


def generate_wav_bytes(duration_s: float, framerate: int = 8000) -> bytes:
    """Generate in-memory WAV audio of given duration."""
    buf = io.BytesIO()
    nframes = int(duration_s * framerate)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x00\x00" * nframes)
    return buf.getvalue()


def test_transcribe_short_audio() -> None:
    reply = {
        "status": 200,
        "headers": {},
        "json": {
            "segments": [
                {"speaker": "worker_1", "text": "Wash hands with soap", "lang": "en"},
                {"speaker": "supervisor", "text": "Check sanitizer", "lang": "en"},
            ]
        },
    }
    transport = ScriptedTransport(reply)
    bus = RecordingBus()
    client = VoiceTranscribeClient(transport, clock=FakeClock(), bus=bus)

    audio = generate_wav_bytes(2.0)
    segments = client.transcribe(
        audio, lang_bias=["en"], keywords=["soap", "sanitizer"], diarize=True
    )

    assert len(segments) == 2
    assert segments[0]["speaker"] == "worker_1"
    assert segments[0]["text"] == "Wash hands with soap"
    assert segments[1]["speaker"] == "supervisor"

    # Check relay telemetry
    assert len(bus.published) == 1
    topic, event = bus.published[0]
    assert topic == Topic.RELAY_MODEL_USED
    assert isinstance(event, ModelUsed)
    assert event.model == "muse-voice-transcribe"
    assert event.segment == "voice"


def test_splitting_files_over_10_minutes() -> None:
    # 12 minutes of audio (720s) -> should be split into 600s + 120s
    audio = generate_wav_bytes(720.0, framerate=1000)
    chunks = split_wav_audio(audio, max_duration_s=600.0)
    assert len(chunks) == 2

    # Verify each chunk is a valid WAV
    with wave.open(io.BytesIO(chunks[0]), "rb") as r:
        assert r.getnframes() == 600 * 1000
    with wave.open(io.BytesIO(chunks[1]), "rb") as r:
        assert r.getnframes() == 120 * 1000

    # Test transcribe calls transport once per split chunk and merges results
    reply_1 = {
        "status": 200,
        "headers": {},
        "json": {"segments": [{"speaker": "spk_1", "text": "Part one", "lang": "en"}]},
    }
    reply_2 = {
        "status": 200,
        "headers": {},
        "json": {"segments": [{"speaker": "spk_1", "text": "Part two", "lang": "en"}]},
    }
    transport = ScriptedTransport(reply_1, reply_2)
    client = VoiceTranscribeClient(transport, clock=FakeClock())

    segments = client.transcribe(audio)
    assert len(segments) == 2
    assert segments[0]["text"] == "Part one"
    assert segments[1]["text"] == "Part two"
    assert len(transport.requests) == 2


def test_streaming_session_keywords_only_at_session_start() -> None:
    session = StreamingVoiceSession(
        session_type="DIARIZATION",
        keywords=["safety", "goggles"],
        language_bias=["en", "es"],
        token="test_stream_handshake_auth",  # noqa: S106
    )

    handshake = session.initial_handshake()
    assert handshake["type"] == "start"
    assert handshake["session_type"] == "DIARIZATION"
    assert handshake["keywords"] == ["safety", "goggles"]
    assert handshake["languageBias"] == ["en", "es"]
    assert handshake["token"] == "test_stream_handshake_auth"  # noqa: S105

    # Any modification after session start must raise ValueError
    with pytest.raises(ValueError, match="keywords can only be set at session start"):
        session.set_keywords(["new_keyword"])

    with pytest.raises(ValueError, match="languageBias can only be set at session start"):
        session.set_language_bias(["fr"])


def test_replay_miss_raises_replay_miss(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = ReplayCache(tmp_path, "replay", clock=clock)
    raw_transport = ScriptedTransport({"status": 200, "headers": {}, "json": {"segments": []}})
    cached_transport = cache.wrap_transport("meta", "muse-voice-transcribe", raw_transport)
    client = VoiceTranscribeClient(cached_transport, clock=clock)

    audio = generate_wav_bytes(1.0)
    with pytest.raises(ReplayMiss):
        client.transcribe(audio)


def test_from_settings() -> None:
    settings_live = Settings(
        rulecam_mode="live",
        meta_api_base_url="https://api.meta.ai/v1",
        meta_api_key=SecretStr("voice-secret"),
    )
    client_live = VoiceTranscribeClient.from_settings(settings_live, clock=FakeClock())
    assert isinstance(client_live, VoiceTranscribeClient)

    settings_missing = Settings(
        rulecam_mode="live",
        meta_api_base_url=None,
        meta_api_key=None,
    )
    with pytest.raises(ClientConfigError):
        VoiceTranscribeClient.from_settings(settings_missing, clock=FakeClock())
