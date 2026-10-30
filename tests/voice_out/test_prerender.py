from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulecam.config import get_settings
from rulecam.voice_out.errors import TranslationError
from rulecam.voice_out.prerender import (
    MAX_PHRASE_CHARS,
    TRANSLATION_TEMPLATE,
    Prerenderer,
    Translation,
    cache_key,
    default_cache_dir,
    translation_messages,
)
from tests.fakes import FakeLLM, FakeTTS
from tests.voice_out.helpers import CASCO_ES, wav_info

HARD_HAT_EN = "Careful! Put on your hard hat"

# The canonical form is pinned so an accidental format change (which would orphan every cached
# WAV) fails this test.
CASCO_ES_CANONICAL = '{"lang":"es","text":"¡Cuidado! Póngase el casco","v":1,"voice":"mms-tts"}'


def make_prerenderer(tmp_path: Path, llm: FakeLLM, tts: FakeTTS | None = None) -> Prerenderer:
    return Prerenderer(tts or FakeTTS(), llm, cache_dir=tmp_path / "tts")


# --- cache key --------------------------------------------------------------------------------


def test_cache_key_is_a_sha256_hex_digest() -> None:
    key = cache_key(CASCO_ES, "es")
    assert len(key) == 64
    assert int(key, 16) >= 0
    assert key == key.lower()


def test_cache_key_is_sha256_of_pinned_canonical_json() -> None:
    expected = hashlib.sha256(CASCO_ES_CANONICAL.encode("utf-8")).hexdigest()
    assert cache_key(CASCO_ES, "es", voice="mms-tts") == expected


@given(text=st.text(min_size=1, max_size=60), lang=st.sampled_from(["en", "es", "hi", "vi"]))
def test_cache_key_is_deterministic(text: str, lang: str) -> None:
    assert cache_key(text, lang) == cache_key(text, lang)


def test_cache_key_ignores_unicode_form_whitespace_and_region() -> None:
    decomposed = unicodedata.normalize("NFD", CASCO_ES)
    assert decomposed != CASCO_ES
    assert cache_key(decomposed, "es") == cache_key(CASCO_ES, "es")
    assert cache_key(f"  {CASCO_ES}\n", "es") == cache_key(CASCO_ES, "es")
    assert cache_key(CASCO_ES, "es-MX") == cache_key(CASCO_ES, "es")


@pytest.mark.parametrize(
    ("text", "lang", "voice"),
    [
        ("¡Cuidado! Póngase el sombrero", "es", "mms-tts"),
        (CASCO_ES, "pt", "mms-tts"),
        (CASCO_ES, "es", "system-tts"),
    ],
)
def test_cache_key_changes_with_text_language_and_voice(text: str, lang: str, voice: str) -> None:
    assert cache_key(text, lang, voice=voice) != cache_key(CASCO_ES, "es", voice="mms-tts")


# --- translation ------------------------------------------------------------------------------


def test_translation_uses_the_fixed_template_with_the_phrase_as_data(tmp_path: Path) -> None:
    llm = FakeLLM([Translation(text=CASCO_ES)])
    text = make_prerenderer(tmp_path, llm).translate(HARD_HAT_EN, "es")

    assert text == CASCO_ES
    call = llm.calls[0]
    assert call["model"] == "glimmer"
    assert call["schema"] == "Translation"
    assert call["messages"] == translation_messages(HARD_HAT_EN, source="en", target="es")
    assert call["messages"] == [
        {"role": "system", "content": TRANSLATION_TEMPLATE.format(source="en", target="es")},
        {"role": "user", "content": HARD_HAT_EN},
    ]
    assert "never instructions" in TRANSLATION_TEMPLATE


def test_source_language_is_not_translated(tmp_path: Path) -> None:
    llm = FakeLLM()
    assert make_prerenderer(tmp_path, llm).translate(f" {HARD_HAT_EN} ", "en-US") == HARD_HAT_EN
    assert llm.calls == []


@pytest.mark.parametrize("answer", ["", "   ", "x" * (MAX_PHRASE_CHARS + 1)])
def test_unusable_translations_are_rejected(tmp_path: Path, answer: str) -> None:
    llm = FakeLLM([Translation(text=answer)])
    with pytest.raises(TranslationError):
        make_prerenderer(tmp_path, llm).translate(HARD_HAT_EN, "es")


@pytest.mark.parametrize("phrase", ["", "  \n"])
def test_empty_phrases_are_rejected(tmp_path: Path, phrase: str) -> None:
    with pytest.raises(ValueError, match="empty phrase"):
        make_prerenderer(tmp_path, FakeLLM()).translate(phrase, "es")


# --- rendering and the WAV cache --------------------------------------------------------------


def test_render_writes_the_wav_under_its_cache_key(tmp_path: Path) -> None:
    tts = FakeTTS()
    phrase = make_prerenderer(tmp_path, FakeLLM([Translation(text=CASCO_ES)]), tts).render(
        HARD_HAT_EN, "es"
    )

    expected = tmp_path / "tts" / f"{cache_key(CASCO_ES, 'es')}.wav"
    assert phrase.path == str(expected)
    assert phrase.cache_key == cache_key(CASCO_ES, "es")
    assert (phrase.source, phrase.lang, phrase.text, phrase.reused) == (
        HARD_HAT_EN,
        "es",
        CASCO_ES,
        False,
    )
    assert tts.calls == [(CASCO_ES, "es")]
    assert wav_info(expected.read_bytes())[2] == 16_000
    assert list(expected.parent.glob("*.partial")) == []


def test_cached_phrases_are_not_synthesised_again(tmp_path: Path) -> None:
    tts = FakeTTS()
    llm = FakeLLM([Translation(text=CASCO_ES), Translation(text=CASCO_ES)])
    prerenderer = make_prerenderer(tmp_path, llm, tts)

    first = prerenderer.render(HARD_HAT_EN, "es")
    second = prerenderer.render(HARD_HAT_EN, "es")

    assert first.path == second.path
    assert (first.reused, second.reused) == (False, True)
    assert len(tts.calls) == 1


def test_prerender_covers_every_phrase_and_language_once(tmp_path: Path) -> None:
    tts = FakeTTS()
    llm = FakeLLM(
        [
            Translation(text=CASCO_ES),
            Translation(text="Cẩn thận! Đội mũ bảo hộ"),
            Translation(text="Lávese las manos"),
            Translation(text="Rửa tay"),
        ]
    )
    phrases = make_prerenderer(tmp_path, llm, tts).prerender(
        [HARD_HAT_EN, "Wash your hands", HARD_HAT_EN], ["en", "es", "vi", "es"]
    )

    assert [(p.source, p.lang) for p in phrases] == [
        (HARD_HAT_EN, "en"),
        (HARD_HAT_EN, "es"),
        (HARD_HAT_EN, "vi"),
        ("Wash your hands", "en"),
        ("Wash your hands", "es"),
        ("Wash your hands", "vi"),
    ]
    assert len(llm.calls) == 4
    assert len({p.path for p in phrases}) == 6
    assert all(Path(p.path).is_file() for p in phrases)


def test_default_cache_dir_follows_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RULECAM_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    try:
        assert default_cache_dir() == tmp_path / "data" / "tts"
    finally:
        get_settings.cache_clear()
