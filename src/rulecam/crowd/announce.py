"""Hear each announcement back before it plays: TTS speaks, Voice Transcribe listens, Spark judges.

A garbled or mistranslated public-address message makes a crowd worse. After MMS-TTS speaks an
announcement, Muse Voice Transcribe transcribes the WAV back (only audio the system synthesised
itself, never a microphone: guardrail R13), and Muse Spark compares what was meant with what was
heard, in each language, and says whether the meaning survived. Word error rate is measured and
handed to Spark as one number among others; Spark decides. The page plays only announcements
whose meaning was confirmed or could not be checked, and shows which is which.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Final

from pydantic import Field

from rulecam.contracts import Frozen, StructuredLLM
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.voice_out.roundtrip import RoundTripChecker

_log = get_logger(__name__)

SPARK_MODEL: Final = "muse-spark-1.3"


class Heard(Frozen):
    """Spark's verdict on one announcement heard back."""

    lang: str
    same_meaning: bool = Field(description="True when what was heard says what was meant.")
    note: str = Field(description="One short sentence: what differs, or 'same meaning'.")


class HeardBack(Frozen):
    """Spark's verdicts, one per announcement."""

    results: list[Heard]


def hear_back(
    spoken: Sequence[dict[str, str]],
    wavs: dict[int, bytes],
    *,
    checker: RoundTripChecker,
    spark: StructuredLLM,
) -> list[dict[str, str]]:
    """Each announcement with what was heard back and whether its meaning survived.

    Args:
        spoken: The announcements (`lang`, `text`, `audio`), in order.
        wavs: Index in `spoken` -> the WAV bytes synthesised for it.
        checker: Voice Transcribe with the word-error-rate score.
        spark: Muse Spark, which judges the meaning.

    Returns:
        The announcements with `heard`, `wer` and `meaning` (`same`, `different` or
        `unchecked`) and `meaning_note`.
    """
    out = [dict(item) for item in spoken]
    for k, wav in wavs.items():
        item = out[k]
        try:
            result = checker.check(wav, item["text"], item["lang"])
        except (RulecamError, OSError, ValueError) as exc:
            _log.warning("crowd_hear_back_failed", lang=item["lang"], error=str(exc)[:200])
            item.update(meaning="unchecked", meaning_note="Voice Transcribe failed")
            continue
        item.update(heard=result.transcript, wer=f"{result.wer:.2f}")
    asked = [
        {"lang": item["lang"], "meant": item["text"], "heard": item["heard"], "wer": item["wer"]}
        for item in out
        if "heard" in item
    ]
    if not asked:
        return out
    try:
        verdict = spark.structured(
            model=SPARK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(asked, ensure_ascii=False)},
            ],
            schema=HeardBack,
        )
    except RulecamError as exc:
        _log.warning("crowd_hear_back_unjudged", error=str(exc)[:200])
        for item in out:
            if "heard" in item:
                item.update(meaning="unchecked", meaning_note="Muse Spark could not judge it")
        return out
    by_lang = {v.lang: v for v in verdict.results}
    for item in out:
        found = by_lang.get(item["lang"]) if "heard" in item else None
        if found is not None:
            item.update(
                meaning="same" if found.same_meaning else "different", meaning_note=found.note
            )
        elif "heard" in item:
            item.update(meaning="unchecked", meaning_note="Muse Spark gave no verdict")
    return out


SYSTEM: Final = (
    "Public-address announcements for a crowd were synthesised to speech and transcribed back. "
    "For each language, compare what was meant with what was heard and say whether a listener "
    "would understand the same instruction. Spelling, script, punctuation and small word "
    "differences do not matter; a changed instruction, place, direction or number does. `wer` "
    "is the word error rate, for reference. Reply with one JSON object."
)


__all__ = ["Heard", "HeardBack", "hear_back"]
