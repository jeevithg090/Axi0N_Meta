from __future__ import annotations

import pytest

from rulecam.voice_out.errors import UnsupportedLanguageError
from rulecam.voice_out.languages import MMS_CODES, mms_code, mms_model_id, primary_subtag


@pytest.mark.parametrize(
    ("iso1", "mms"),
    [
        ("en", "eng"),
        ("es", "spa"),
        ("hi", "hin"),
        ("pt", "por"),
        ("fr", "fra"),
        ("vi", "vie"),
        ("de", "deu"),
        ("tl", "tgl"),
        ("ar", "ara"),
        ("ko", "kor"),
        ("ms", "zlm"),
        ("sw", "swh"),
    ],
)
def test_language_table_maps_iso639_1_to_mms(iso1: str, mms: str) -> None:
    assert MMS_CODES[iso1] == mms
    assert mms_code(iso1) == mms


def test_language_table_shape() -> None:
    assert len(MMS_CODES) >= 6
    for iso1, mms in MMS_CODES.items():
        assert len(iso1) == 2
        assert iso1.isascii()
        assert iso1.islower()
        assert len(mms) == 3
        assert mms.isascii()
        assert mms.islower()
    assert len(set(MMS_CODES.values())) == len(MMS_CODES), "two tags map to one voice"


def test_language_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        MMS_CODES["xx"] = "xxx"  # type: ignore[index]


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("es", "es"), ("es-MX", "es"), ("pt_BR", "pt"), (" ES ", "es"), ("zh-Hant-TW", "zh")],
)
def test_primary_subtag(tag: str, expected: str) -> None:
    assert primary_subtag(tag) == expected


@pytest.mark.parametrize(("tag", "expected"), [("es-MX", "spa"), ("PT-br", "por"), ("EN", "eng")])
def test_region_and_case_are_ignored(tag: str, expected: str) -> None:
    assert mms_code(tag) == expected


@pytest.mark.parametrize("code", ["spa", "eng", "hin"])
def test_mms_codes_pass_through(code: str) -> None:
    assert mms_code(code) == code


@pytest.mark.parametrize("tag", ["it", "ja", "zh", "ur", "xx", "", "español"])
def test_languages_without_an_mms_voice_raise(tag: str) -> None:
    with pytest.raises(UnsupportedLanguageError):
        mms_code(tag)


def test_model_id() -> None:
    assert mms_model_id("es") == "facebook/mms-tts-spa"
    assert mms_model_id("spa") == "facebook/mms-tts-spa"
