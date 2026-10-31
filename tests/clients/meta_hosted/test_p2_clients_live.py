"""Live checks for the Spark, Voice Transcribe and Muse Image clients (`make live-test`; never CI).

The unit tests use replies in the documented shapes. These checks confirm the shapes against the
real API. Run with `RULECAM_MODE=live META_API_KEY=… META_API_BASE_URL=https://api.meta.ai/v1`.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest
from pydantic import BaseModel

from rulecam.clients.meta_hosted.image import MuseImageClient
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.clients.meta_hosted.voice import VoiceTranscribeClient
from rulecam.config import Settings
from tests.fakes import FakeClock

pytestmark = pytest.mark.live


class Answer(BaseModel):
    city: str


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(rulecam_replay_dir=tmp_path / "replay", rulecam_data_dir=tmp_path)
    if settings.rulecam_mode == "replay" or settings.meta_api_key is None:
        pytest.skip("needs RULECAM_MODE=live and META_API_KEY")
    return settings


def test_live_spark_structured_output(tmp_path: Path) -> None:
    client = SparkClient.from_settings(_settings(tmp_path), clock=FakeClock())
    answer = client.structured(
        messages=[{"role": "user", "content": "Which city is Meta headquartered in? JSON only."}],
        schema=Answer,
    )
    assert answer.city


def test_live_spark_reads_an_uploaded_text_file(tmp_path: Path) -> None:
    client = SparkClient.from_settings(_settings(tmp_path), clock=FakeClock())
    doc = tmp_path / "note.txt"
    doc.write_text("The site is in Lisbon.", encoding="utf-8")
    content: list[object] = [{"type": "input_text", "text": "Which city? JSON only."}]
    content.append(client.attach(doc))
    answer = client.structured(messages=[{"role": "user", "content": content}], schema=Answer)
    assert "lisbon" in answer.city.lower()


def test_live_voice_transcribe_accepts_a_wav(tmp_path: Path) -> None:
    client = VoiceTranscribeClient.from_settings(_settings(tmp_path), clock=FakeClock())
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\x00\x00" * 16_000)
    assert client.transcribe(buf.getvalue(), lang_bias=["en"], diarize=True) == []


def test_live_muse_image_generates_a_png(tmp_path: Path) -> None:
    client = MuseImageClient.from_settings(_settings(tmp_path), clock=FakeClock())
    result = client.generate("a plain red square", size="512x512")
    assert result.path.stat().st_size > 0
    assert result.response_id
