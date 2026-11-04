"""Announcements heard back: Voice Transcribe listens, Spark judges whether the meaning survived."""

from __future__ import annotations

from rulecam.crowd.announce import hear_back
from rulecam.errors import RulecamError
from rulecam.voice_out.roundtrip import RoundTripChecker
from tests.fakes import FakeLLM


class Ears:
    """Voice Transcribe stand-in: hears a fixed text per language bias."""

    def __init__(self, heard: dict[str, str]) -> None:
        self.heard = heard

    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        lang = (lang_bias or ["en"])[0]
        if lang == "xx":
            msg = "no such language"
            raise RulecamError(msg)
        return [{"speaker": "", "text": self.heard[lang], "lang": lang}]


SPOKEN = [
    {"lang": "hi", "text": "कृपया रुकिए, गेट चार खुला है।", "audio": "a-0-hi.wav"},
    {"lang": "en", "text": "Please wait; gate four is open.", "audio": "a-1-en.wav"},
    {"lang": "xx", "text": "?", "audio": "a-2-xx.wav"},
    {"lang": "fr", "text": "Attendez."},
]


def test_spark_judges_each_announcement_heard_back() -> None:
    ears = Ears({"hi": "कृपया रुकिए गेट चार खुला है", "en": "Please wait gate five is open"})
    spark = FakeLLM(
        [
            {
                "results": [
                    {"lang": "hi", "same_meaning": True, "note": "same meaning"},
                    {"lang": "en", "same_meaning": False, "note": "gate five, not four"},
                ]
            }
        ]
    )
    out = hear_back(
        SPOKEN, {0: b"RIFF", 1: b"RIFF", 2: b"RIFF"}, checker=RoundTripChecker(ears), spark=spark
    )
    assert [o.get("meaning") for o in out] == ["same", "different", "unchecked", None]
    assert out[1]["meaning_note"] == "gate five, not four"
    assert out[1]["heard"] == "Please wait gate five is open"
    assert float(out[1]["wer"]) > 0
    assert "fr" not in str(spark.calls[0]["messages"][1]["content"])  # type: ignore[index]


def test_a_failing_spark_leaves_them_unchecked() -> None:
    ears = Ears({"en": "Please wait gate four is open"})

    class Down:
        def structured(self, **_: object) -> object:
            msg = "down"
            raise RulecamError(msg)

    out = hear_back(SPOKEN[1:2], {0: b"RIFF"}, checker=RoundTripChecker(ears), spark=Down())  # type: ignore[arg-type]
    assert out[0]["meaning"] == "unchecked"
    assert hear_back(SPOKEN[3:], {}, checker=RoundTripChecker(ears), spark=Down()) == [SPOKEN[3]]  # type: ignore[arg-type]
