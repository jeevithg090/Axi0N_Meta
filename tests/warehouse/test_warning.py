"""A bilingual spoken warning: each language spoken and checked, one WAV playing both."""

from __future__ import annotations

import io
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

from rulecam.contracts import EventRecord
from rulecam.warehouse.models import WarningDraft
from rulecam.warehouse.talks import Store, WarningMaker, incident_from_records, join_wavs
from tests.fakes import FakeClock
from tests.warehouse.conftest import T02_EVENTS

M = TypeVar("M", bound=BaseModel)
NOW = datetime(2026, 11, 4, 2, 0, tzinfo=UTC)


def _wav(seconds: float, rate: int = 16_000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x01\x00" * int(rate * seconds))
    return out.getvalue()


class Spark:
    def __init__(self) -> None:
        self.system = ""

    def structured(
        self,
        *,
        model: str = "muse-spark-1.3",
        messages: list[dict[str, object]],
        schema: type[M],
        camera_id: str | None = None,
    ) -> M:
        self.system = str(messages[0]["content"])
        draft = WarningDraft(
            text="¡Montacargas acercándose!",
            english="Forklift coming!",
            also_text="Forklift coming!",
        )
        return schema.model_validate(draft.model_dump())


class Tts:
    def synth(self, text: str, lang: str) -> bytes:
        return _wav(1.0 if lang == "es" else 0.5)


class Heard:
    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        heard = {"es": "Montacargas acercándose", "en": "Forklift coming"}
        return [{"text": heard[(lang_bias or ["en"])[0]]}]


def test_bilingual_warning_speaks_and_checks_each_language(
    tmp_path: Path, t02_records: list[EventRecord]
) -> None:
    spark = Spark()
    store = Store(tmp_path / "w")
    maker = WarningMaker(
        spark, store=store, clock=FakeClock(NOW), tts=[("Meta MMS-TTS", Tts())], transcriber=Heard()
    )
    incident = incident_from_records(t02_records, T02_EVENTS[1], None)
    assert incident is not None
    warning = maker.make(incident, "es", also="en")
    assert "language with code en" in spark.system
    assert [(p.lang, p.roundtrip_ok) for p in warning.parts] == [("es", True), ("en", True)]
    assert warning.text == "¡Montacargas acercándose!"
    assert warning.audio == f"warning-{T02_EVENTS[1]}-es_en.wav"
    stored = store.file(warning.audio)
    assert stored is not None
    with wave.open(io.BytesIO(stored.read_bytes()), "rb") as wav:
        assert wav.getnframes() == pytest.approx(16_000 * (1.0 + 0.4 + 0.5), abs=2)
    assert store.load("warnings", f"{T02_EVENTS[1]}.es+en", type(warning)) == warning
    single = maker.make(incident, "es")
    assert len(single.parts) == 1


def test_join_wavs_refuses_mismatched_formats() -> None:
    assert join_wavs([_wav(0.1)]) == _wav(0.1)
    with pytest.raises(ValueError, match="differ"):
        join_wavs([_wav(0.1), _wav(0.1, rate=22_050)])
