"""Round-trip check: transcribe each pre-rendered phrase back and score it by word error rate.

A garbled or mistranslated phrase must never reach a loudspeaker. Each WAV is transcribed with
an injected `Transcriber` (Muse Voice Transcribe in the product) and compared with the text that
was meant to be spoken, after lower-casing and stripping punctuation and accents. A phrase passes
if WER ≤ 0.25; otherwise its result carries the reason `voice_roundtrip_failed` for Rule CI.

This only ever transcribes audio Voice Out synthesised itself, never microphone or camera audio
(guardrail R13: no ambient speech transcription).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal

from pydantic import Field

from rulecam.contracts import Frozen, Transcriber
from rulecam.log import get_logger
from rulecam.voice_out.languages import primary_subtag
from rulecam.voice_out.prerender import PrerenderedPhrase

_log = get_logger(__name__)

PASS_MAX_WER: Final = 0.25
ROUNDTRIP_FAILED: Final = "voice_roundtrip_failed"


class RoundTripResult(Frozen):
    """Outcome of transcribing one phrase back.

    Attributes:
        text: What was meant to be spoken.
        lang: Language tag of the phrase.
        transcript: What the transcriber heard.
        wer: Word error rate of `transcript` against `text`, after normalisation.
        passed: True if `wer` ≤ the threshold.
        reason: `"voice_roundtrip_failed"` when the phrase failed, for Rule CI (P2-08).
    """

    text: str
    lang: str
    transcript: str
    wer: float = Field(ge=0)
    passed: bool
    reason: Literal["voice_roundtrip_failed"] | None = None


def normalize_words(text: str) -> list[str]:
    """Split text into comparable words: case-folded, without accents or punctuation.

    Args:
        text: Any text, e.g. `"¡Cuidado! Póngase el casco"`.

    Returns:
        The words, e.g. `["cuidado", "pongase", "el", "casco"]`.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    kept = (
        " " if unicodedata.category(char)[0] in "PSZ" else char
        for char in decomposed
        if unicodedata.category(char) != "Mn"
    )
    return "".join(kept).split()


def edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    """Return the Levenshtein distance between two word sequences.

    Args:
        reference: Intended words.
        hypothesis: Heard words.

    Returns:
        The minimum number of substitutions, deletions and insertions.
    """
    previous = list(range(len(hypothesis) + 1))
    for i, ref_word in enumerate(reference, start=1):
        current = [i]
        for j, hyp_word in enumerate(hypothesis, start=1):
            substitution = previous[j - 1] + (ref_word != hyp_word)
            current.append(min(previous[j] + 1, current[j - 1] + 1, substitution))
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Return the word error rate of `hypothesis` against `reference` after normalisation.

    Args:
        reference: The intended text.
        hypothesis: The transcribed text.

    Returns:
        Edit distance divided by the number of reference words (can exceed 1).

    Raises:
        ValueError: If `reference` has no words.
    """
    ref_words = normalize_words(reference)
    if not ref_words:
        msg = "reference text has no words"
        raise ValueError(msg)
    return edit_distance(ref_words, normalize_words(hypothesis)) / len(ref_words)


def score_roundtrip(
    text: str, lang: str, transcript: str, *, max_wer: float = PASS_MAX_WER
) -> RoundTripResult:
    """Score a transcript against the intended text.

    Args:
        text: What was meant to be spoken.
        lang: Language tag.
        transcript: What was heard.
        max_wer: Highest WER that still passes.

    Returns:
        The typed result; failures carry `reason="voice_roundtrip_failed"`.

    Raises:
        ValueError: If `text` has no words.
    """
    wer = word_error_rate(text, transcript)
    passed = wer <= max_wer
    return RoundTripResult(
        text=text,
        lang=lang,
        transcript=transcript,
        wer=wer,
        passed=passed,
        reason=None if passed else ROUNDTRIP_FAILED,
    )


class RoundTripChecker:
    """Transcribes synthesised phrases back and scores them."""

    def __init__(self, transcriber: Transcriber, *, max_wer: float = PASS_MAX_WER) -> None:
        """Create a checker.

        Args:
            transcriber: Speech to text (Voice Transcribe in the product).
            max_wer: Highest WER that still passes.
        """
        self._transcriber = transcriber
        self._max_wer = max_wer

    def check(self, audio: bytes, text: str, lang: str) -> RoundTripResult:
        """Transcribe `audio` and score it against `text`.

        Args:
            audio: WAV bytes produced by Voice Out.
            text: What the audio is meant to say.
            lang: Language tag, passed to the transcriber as its only language bias.

        Returns:
            The typed result.

        Raises:
            ValueError: If `text` has no words.
        """
        segments = self._transcriber.transcribe(audio, lang_bias=[primary_subtag(lang)])
        transcript = " ".join(segment.get("text", "").strip() for segment in segments).strip()
        result = score_roundtrip(text, lang, transcript, max_wer=self._max_wer)
        if not result.passed:
            _log.warning(ROUNDTRIP_FAILED, lang=lang, wer=round(result.wer, 3))
        return result

    def check_phrase(self, phrase: PrerenderedPhrase) -> RoundTripResult:
        """Check one cached phrase from `Prerenderer`.

        Args:
            phrase: The pre-rendered phrase; its WAV is read from `phrase.path`.

        Returns:
            The typed result.
        """
        return self.check(Path(phrase.path).read_bytes(), phrase.text, phrase.lang)
