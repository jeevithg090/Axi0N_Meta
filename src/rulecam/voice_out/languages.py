"""ISO-639-1 to MMS-TTS language codes.

MMS-TTS checkpoints are published as `facebook/mms-tts-<code>`, where `<code>` is ISO-639-3.
Every checkpoint below was confirmed on the Hugging Face Hub on 2026-10-30. Some major languages
(Italian, Japanese, Mandarin, Urdu) have no MMS-TTS checkpoint and must use another engine.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from rulecam.voice_out.errors import UnsupportedLanguageError

MMS_MODEL_PREFIX: Final = "facebook/mms-tts-"

MMS_CODES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "am": "amh",
        "ar": "ara",
        "bn": "ben",
        "de": "deu",
        "el": "ell",
        "en": "eng",
        "es": "spa",
        "fr": "fra",
        "gu": "guj",
        "ha": "hau",
        "hi": "hin",
        "id": "ind",
        "ko": "kor",
        "mr": "mar",
        "ms": "zlm",
        "nl": "nld",
        "pa": "pan",
        "pl": "pol",
        "pt": "por",
        "ro": "ron",
        "ru": "rus",
        "sw": "swh",
        "ta": "tam",
        "te": "tel",
        "th": "tha",
        "tl": "tgl",
        "tr": "tur",
        "uk": "ukr",
        "vi": "vie",
        "yo": "yor",
    }
)

_KNOWN_MMS_CODES: Final = frozenset(MMS_CODES.values())
_TAG_SEPARATOR: Final = re.compile(r"[-_]")


def primary_subtag(lang: str) -> str:
    """Return the lower-cased primary language subtag of a BCP-47 style tag.

    Args:
        lang: A tag such as `"es"`, `"es-MX"`, `"pt_BR"` or `"ES"`.

    Returns:
        The primary subtag, e.g. `"es"`.
    """
    return _TAG_SEPARATOR.split(lang.strip().lower(), maxsplit=1)[0]


def mms_code(lang: str) -> str:
    """Map a language tag to its MMS-TTS (ISO-639-3) code.

    Args:
        lang: An ISO-639-1 tag (optionally with a region, e.g. `"es-MX"`) or an MMS code itself.

    Returns:
        The MMS code, e.g. `"spa"` for `"es"`.

    Raises:
        UnsupportedLanguageError: If MMS-TTS has no checkpoint for the language.
    """
    tag = primary_subtag(lang)
    if tag in MMS_CODES:
        return MMS_CODES[tag]
    if tag in _KNOWN_MMS_CODES:
        return tag
    msg = f"MMS-TTS has no voice for language {lang!r}"
    raise UnsupportedLanguageError(msg)


def mms_model_id(lang: str) -> str:
    """Return the Hugging Face model id of the MMS-TTS checkpoint for a language.

    Args:
        lang: A language tag accepted by `mms_code`.

    Returns:
        A model id such as `"facebook/mms-tts-spa"`.

    Raises:
        UnsupportedLanguageError: If MMS-TTS has no checkpoint for the language.
    """
    return f"{MMS_MODEL_PREFIX}{mms_code(lang)}"
