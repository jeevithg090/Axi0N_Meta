from __future__ import annotations

import subprocess
import sys

import pytest

from rulecam.bus import Topic
from rulecam.contracts import TTS
from rulecam.voice_out.errors import SynthesisError, UnsupportedLanguageError
from rulecam.voice_out.relay import ModelUsed
from rulecam.voice_out.tts import FallbackTts, MmsTts, Synthesizer, _import_ml, needs_romanisation
from rulecam.voice_out.wav import Samples
from tests.fakes import FakeTTS
from tests.voice_out.helpers import CASCO_ES, RecordingBus, StepTimer, tone, wav_info


class FakeLoader:
    """Builds tone synthesizers at a chosen rate and records which languages were loaded."""

    def __init__(self, sample_rate: int = 22_050) -> None:
        self.sample_rate = sample_rate
        self.loaded: list[str] = []
        self.spoken: list[tuple[str, str]] = []

    def __call__(self, code: str) -> Synthesizer:
        self.loaded.append(code)

        def synthesize(text: str) -> tuple[Samples, int]:
            self.spoken.append((code, text))
            return tone(0.5, self.sample_rate), self.sample_rate

        return synthesize


class FailingTTS:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def synth(self, text: str, lang: str) -> bytes:
        self.calls += 1
        raise self.error


def test_engines_satisfy_the_tts_protocol(bus: RecordingBus) -> None:
    mms: TTS = MmsTts(bus, loader=FakeLoader())
    chained: TTS = FallbackTts(mms, FakeTTS())
    assert chained.synth("hello", "en")


def test_models_load_lazily(bus: RecordingBus) -> None:
    loader = FakeLoader()
    MmsTts(bus, loader=loader)
    assert loader.loaded == []


def test_models_are_cached_per_language(bus: RecordingBus) -> None:
    loader = FakeLoader()
    tts = MmsTts(bus, loader=loader)

    tts.synth(CASCO_ES, "es")
    tts.synth("Lávese las manos", "es-MX")
    tts.synth("Wear your hard hat", "en")
    tts.synth("Wash your hands", "en")

    assert loader.loaded == ["spa", "eng"]
    assert [code for code, _ in loader.spoken] == ["spa", "spa", "eng", "eng"]


def test_output_is_a_16k_wav(bus: RecordingBus) -> None:
    audio = MmsTts(bus, loader=FakeLoader(sample_rate=22_050)).synth(CASCO_ES, "es")
    channels, width, rate, n_frames = wav_info(audio)
    assert (channels, width, rate) == (1, 2, 16_000)
    assert abs(n_frames - 8_000) <= 1


def test_publishes_meta_model_used(bus: RecordingBus) -> None:
    tts = MmsTts(bus, loader=FakeLoader(), segment="uc1", timer=StepTimer(step=0.25))
    tts.synth(CASCO_ES, "es")

    assert [m.topic for m in bus.published] == [Topic.RELAY_MODEL_USED]
    assert bus.models_used() == [
        ModelUsed(model="facebook/mms-tts-spa", segment="uc1", ms=250, meta=True)
    ]


def test_unsupported_language_fails_before_loading(bus: RecordingBus) -> None:
    loader = FakeLoader()
    with pytest.raises(UnsupportedLanguageError):
        MmsTts(bus, loader=loader).synth("Indossa il casco", "it")
    assert loader.loaded == []
    assert bus.published == []


def test_fallback_is_used_when_primary_cannot_speak() -> None:
    primary = FailingTTS(UnsupportedLanguageError("no voice"))
    fallback = FakeTTS()

    audio = FallbackTts(primary, fallback).synth("Indossa il casco", "it")

    assert primary.calls == 1
    assert fallback.calls == [("Indossa il casco", "it")]
    assert wav_info(audio)[2] == 16_000


def test_fallback_is_unused_when_primary_succeeds(bus: RecordingBus) -> None:
    fallback = FakeTTS()
    FallbackTts(MmsTts(bus, loader=FakeLoader()), fallback).synth(CASCO_ES, "es")
    assert fallback.calls == []


def test_fallback_does_not_hide_programming_errors() -> None:
    with pytest.raises(RuntimeError):
        FallbackTts(FailingTTS(RuntimeError("bug")), FakeTTS()).synth("x", "en")


@pytest.mark.parametrize(
    ("text", "is_uroman", "expected"),
    [
        ("안전모를 쓰세요", True, True),
        ("anjeonmoreul sseuseyo", True, False),
        (CASCO_ES, False, False),
        ("안전모를 쓰세요", False, False),
    ],
)
def test_needs_romanisation(text: str, is_uroman: bool, expected: bool) -> None:
    assert needs_romanisation(text, is_uroman=is_uroman) is expected


def test_missing_ml_extra_is_explained() -> None:
    with pytest.raises(SynthesisError, match="uv sync --extra ml"):
        _import_ml("rulecam_module_that_does_not_exist")


def test_importing_voice_out_does_not_import_torch() -> None:
    probe = (
        "import sys, rulecam.voice_out; "
        "loaded = {'torch', 'transformers'} & set(sys.modules); "
        "sys.exit(f'imported {sorted(loaded)}' if loaded else 0)"
    )
    result = subprocess.run(  # noqa: S603 - the current interpreter with a fixed script
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
