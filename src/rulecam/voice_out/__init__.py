"""Meta Voice Out: spoken warnings with MMS-TTS, pre-rendered and checked by a round trip.

Importing this package never imports `torch` or `transformers`; MMS models load lazily.
"""

from rulecam.voice_out.errors import (
    SynthesisError,
    TranslationError,
    UnsupportedLanguageError,
    VoiceOutError,
)
from rulecam.voice_out.languages import MMS_CODES, mms_code, mms_model_id
from rulecam.voice_out.prerender import PrerenderedPhrase, Prerenderer, cache_key
from rulecam.voice_out.relay import ModelUsed
from rulecam.voice_out.roundtrip import (
    PASS_MAX_WER,
    ROUNDTRIP_FAILED,
    RoundTripChecker,
    RoundTripResult,
    word_error_rate,
)
from rulecam.voice_out.system_tts import SystemTts
from rulecam.voice_out.tts import FallbackTts, MmsTts, VitsLoader

__all__ = [
    "MMS_CODES",
    "PASS_MAX_WER",
    "ROUNDTRIP_FAILED",
    "FallbackTts",
    "MmsTts",
    "ModelUsed",
    "PrerenderedPhrase",
    "Prerenderer",
    "RoundTripChecker",
    "RoundTripResult",
    "SynthesisError",
    "SystemTts",
    "TranslationError",
    "UnsupportedLanguageError",
    "VitsLoader",
    "VoiceOutError",
    "cache_key",
    "mms_code",
    "mms_model_id",
    "word_error_rate",
]
