from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulecam.voice_out.prerender import Prerenderer
from rulecam.voice_out.roundtrip import (
    PASS_MAX_WER,
    ROUNDTRIP_FAILED,
    RoundTripChecker,
    edit_distance,
    normalize_words,
    score_roundtrip,
    word_error_rate,
)
from tests.fakes import FakeLLM, FakeTranscriber, FakeTTS
from tests.voice_out.helpers import CASCO_ES

WORDS = st.lists(st.sampled_from(["casco", "el", "pongase", "cuidado", "manos"]), max_size=8)


def heard(*texts: str) -> FakeTranscriber:
    return FakeTranscriber([{"speaker": "A", "text": text, "lang": "es"} for text in texts])


# --- normalisation and WER --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "words"),
    [
        (CASCO_ES, ["cuidado", "pongase", "el", "casco"]),
        ("CUIDADO, póngase el casco.", ["cuidado", "pongase", "el", "casco"]),
        ("Rửa tay!", ["rua", "tay"]),
        ("Niño — señal", ["nino", "senal"]),
        ("Don't run…", ["don", "t", "run"]),
        ("  ", []),
    ],
)
def test_normalize_words(text: str, words: list[str]) -> None:
    assert normalize_words(text) == words


@pytest.mark.parametrize(
    ("reference", "hypothesis", "wer"),
    [
        (CASCO_ES, CASCO_ES, 0.0),
        (CASCO_ES, "cuidado pongase el casco", 0.0),
        (CASCO_ES, "Cuidado, póngase el casco.", 0.0),
        (CASCO_ES, "cuidado pongase el caso", 0.25),
        (CASCO_ES, "cuidado ponga el caso", 0.5),
        (CASCO_ES, "cuidado el casco", 0.25),
        (CASCO_ES, "cuidado pongase usted el casco", 0.25),
        (CASCO_ES, "", 1.0),
        ("wash hands", "wash your hands now please", 1.5),
        ("the cat sat on the mat", "the cat sat on mat", 1 / 6),
    ],
)
def test_word_error_rate_on_known_pairs(reference: str, hypothesis: str, wer: float) -> None:
    assert word_error_rate(reference, hypothesis) == pytest.approx(wer)


def test_word_error_rate_needs_a_reference() -> None:
    with pytest.raises(ValueError, match="no words"):
        word_error_rate("¡!", "anything")


@given(a=WORDS, b=WORDS)
def test_edit_distance_is_a_bounded_symmetric_metric(a: list[str], b: list[str]) -> None:
    distance = edit_distance(a, b)
    assert distance == edit_distance(b, a)
    assert abs(len(a) - len(b)) <= distance <= max(len(a), len(b))
    assert (distance == 0) == (a == b)


# --- scoring ----------------------------------------------------------------------------------


def test_threshold_is_inclusive() -> None:
    assert PASS_MAX_WER == 0.25
    at_threshold = score_roundtrip(CASCO_ES, "es", "cuidado pongase el caso")
    assert at_threshold.wer == pytest.approx(0.25)
    assert at_threshold.passed
    assert at_threshold.reason is None


def test_failures_carry_the_rule_ci_reason() -> None:
    result = score_roundtrip(CASCO_ES, "es", "cuidado ponga el caso")
    assert not result.passed
    assert result.reason == ROUNDTRIP_FAILED == "voice_roundtrip_failed"


def test_threshold_is_configurable() -> None:
    assert score_roundtrip(CASCO_ES, "es", "cuidado ponga el caso", max_wer=0.5).passed


# --- the checker with a fake transcriber ------------------------------------------------------


def test_roundtrip_passes_when_the_phrase_is_heard() -> None:
    transcriber = heard("Cuidado.", "Póngase el casco")
    result = RoundTripChecker(transcriber).check(b"RIFF....", CASCO_ES, "es-MX")

    assert result.passed
    assert result.wer == 0.0
    assert result.transcript == "Cuidado. Póngase el casco"
    assert (result.text, result.lang, result.reason) == (CASCO_ES, "es-MX", None)
    assert transcriber.calls == [
        {"n_bytes": 8, "lang_bias": ["es"], "keywords": None, "diarize": False}
    ]


def test_roundtrip_fails_when_the_phrase_is_garbled() -> None:
    result = RoundTripChecker(heard("kwee da do pon gah")).check(b"RIFF", CASCO_ES, "es")
    assert not result.passed
    assert result.wer > PASS_MAX_WER
    assert result.reason == ROUNDTRIP_FAILED


def test_roundtrip_fails_when_nothing_is_heard() -> None:
    result = RoundTripChecker(heard()).check(b"RIFF", CASCO_ES, "es")
    assert result.transcript == ""
    assert result.wer == 1.0
    assert result.reason == ROUNDTRIP_FAILED


def test_check_phrase_reads_the_cached_wav(tmp_path: Path) -> None:
    phrase = Prerenderer(FakeTTS(), FakeLLM(), cache_dir=tmp_path, source_lang="es").render(
        CASCO_ES, "es"
    )
    transcriber = heard("cuidado póngase el casco")

    result = RoundTripChecker(transcriber).check_phrase(phrase)

    assert result.passed
    assert transcriber.calls[0]["n_bytes"] == Path(phrase.path).stat().st_size
