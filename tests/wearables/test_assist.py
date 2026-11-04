"""Tests for the glasses assistant: look, count, compose, speak, and the fallbacks."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from pydantic import BaseModel

from rulecam.contracts import Detection, Mask
from rulecam.shelf.gaps import ShelfScanner
from rulecam.wearables.assist import (
    GAPS_CAPTION,
    NOT_HEARD,
    Ask,
    PovAssistant,
    Voice,
    WearablesError,
    concepts,
    fallback_spoken,
)
from rulecam.wearables.schemas import GlimmerLook
from tests.fakes import EPOCH, FakeClock, FakeLLM, FakeTranscriber, FakeTTS

GLIMMER = "muse-glimmer-30b"


def jpeg() -> bytes:
    ok, data = cv2.imencode(".jpg", np.full((120, 160, 3), 128, dtype=np.uint8))
    assert ok
    return data.tobytes()


def look(
    *, alert: bool = False, hazards: list[str] | None = None, count: list[str] | None = None
) -> dict[str, Any]:
    return {
        "scene": "A warehouse aisle with racking.",
        "answer": "A forklift is ahead.",
        "hazards": hazards or [],
        "alert": alert,
        "count": count or [],
    }


def composed(*, alert: bool = True, spoken: str = "Stop, forklift ahead.") -> dict[str, Any]:
    return {
        "spoken": spoken,
        "detail": "Glimmer and SAM 3.1 both see one forklift ahead.",
        "alert": alert,
        "severity": "danger" if alert else "none",
        "hazards": [{"what": "forklift", "where": "ahead", "severity": "danger"}] if alert else [],
        "confidence": 0.9,
    }


class Files:
    def __init__(self) -> None:
        self.attached: list[str] = []

    def attach(self, path: Path) -> dict[str, object]:
        self.attached.append(path.name)
        return {"type": "input_image", "file_id": path.name}


class Sam:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts
        self.calls: list[str] = []

    def segment_image(
        self, frame: Path, concept: str, *, camera_id: str = "", include_confidence: bool = True
    ) -> list[Detection]:
        self.calls.append(concept)
        return [
            Detection(
                camera_id=camera_id,
                frame_idx=0,
                ts=EPOCH,
                concept=concept,
                object_id=k,
                mask=Mask(rle="", box_xyxy=(10.0 * k, 10.0, 10.0 * k + 20, 60.0), confidence=0.8),
            )
            for k in range(self.counts.get(concept, 0))
        ]


class SlowLLM:
    """Never answers in time."""

    def structured(
        self, *, model: str, messages: list[dict[str, object]], schema: type[BaseModel], **_: object
    ) -> BaseModel:
        time.sleep(0.3)
        raise AssertionError("too late")


def assistant(
    tmp_path: Path,
    *,
    glimmer: Any,
    spark: FakeLLM,
    sam: Sam | None = None,
    transcriber: FakeTranscriber | None = None,
    timeout_s: float = 5.0,
    glimmer_up: bool = False,
) -> tuple[PovAssistant, Files, FakeTTS]:
    files, tts = Files(), FakeTTS()
    svc = PovAssistant(
        spark=spark,
        files=files,
        sam=sam or Sam({}),
        out_dir=tmp_path,
        clock=FakeClock(),
        glimmer=glimmer,
        glimmer_model=GLIMMER,
        transcriber=transcriber,
        voice=Voice(tts, "test voice"),
        glimmer_timeout_s=timeout_s,
    )
    if glimmer_up:
        svc.mark_glimmer(up=True)
    return svc, files, tts


def test_concepts_are_cleaned_deduplicated_and_capped() -> None:
    picked = GlimmerLook.model_validate(
        look(count=[" Forklift. ", "forklift", "", "person  in lane", "pallet", "rack"])
    )
    assert concepts(picked) == ["forklift", "person in lane", "pallet"]
    assert concepts(None) == []


def test_fallback_spoken() -> None:
    assert fallback_spoken(None).startswith("Sorry")
    assert "not checked" in fallback_spoken(GlimmerLook.model_validate(look()))


async def test_question_looks_counts_composes_and_speaks(tmp_path: Path) -> None:
    glimmer = FakeLLM([look(alert=True, hazards=["forklift ahead"], count=["forklift", "person"])])
    spark = FakeLLM([composed()])
    sam = Sam({"forklift": 1})
    svc, files, tts = assistant(tmp_path, glimmer=glimmer, spark=spark, sam=sam, glimmer_up=True)

    answer = await svc.answer(Ask(image=jpeg(), mode="warehouse", question="Safe to walk?"))

    assert answer.kind == "question"
    assert answer.spoken == "Stop, forklift ahead."
    assert answer.verified
    assert answer.verified_by == ["muse-spark-1.3", GLIMMER, "sam-3.1"]
    assert answer.looked_by == GLIMMER
    assert [c.count for c in answer.counts] == [1, 0]
    assert sorted(sam.calls) == ["forklift", "person"]
    assert answer.marked is not None
    assert (svc.frames_dir / answer.marked).is_file()
    assert answer.audio is not None
    assert (svc.audio_dir / answer.audio).is_file()
    assert tts.calls == [("Stop, forklift ahead.", "en")]
    assert [s.name for s in answer.stages] == ["look", "count", "compose", "speak"]
    assert files.attached == [answer.frame]
    text = spark.calls[0]["messages"][1]["content"][0]["text"]  # type: ignore[index]
    assert "Safe to walk?" in text
    assert '"forklift"' in text


async def test_quiet_patrol_frame_costs_one_glimmer_call(tmp_path: Path) -> None:
    spark = FakeLLM([])
    svc, files, tts = assistant(tmp_path, glimmer=FakeLLM([look()]), spark=spark)

    answer = await svc.answer(Ask(image=jpeg(), mode="warehouse"))

    assert answer.kind == "patrol"
    assert answer.spoken == ""
    assert answer.audio is None
    assert spark.calls == []
    assert files.attached == []
    assert tts.calls == []


async def test_patrol_alert_is_spoken_only_when_verified(tmp_path: Path) -> None:
    glimmer = FakeLLM([look(alert=True, hazards=["spill"]), look()])
    spark = FakeLLM([composed(spoken="Spill ahead left.")])
    svc, _, _ = assistant(tmp_path, glimmer=glimmer, spark=spark)

    answer = await svc.answer(Ask(image=jpeg(), mode="aisle"))

    assert answer.verified
    assert answer.spoken == "Spill ahead left."
    assert answer.verified_by == ["muse-spark-1.3", GLIMMER]


async def test_glimmer_timeout_lets_spark_look_and_nothing_is_verified(tmp_path: Path) -> None:
    spark = FakeLLM([look(alert=True, hazards=["forklift"]), composed()])
    svc, _, _ = assistant(tmp_path, glimmer=SlowLLM(), spark=spark, timeout_s=0.05)

    patrol = await svc.answer(Ask(image=jpeg(), mode="warehouse"))

    assert patrol.looked_by == "muse-spark-1.3 (Glimmer unavailable)"
    assert patrol.alert
    assert not patrol.verified
    assert patrol.spoken == ""  # an unverified patrol alert is shown, not spoken
    looks = [s for s in patrol.stages if s.name == "look"]
    assert [(s.model, s.ok) for s in looks] == [(GLIMMER, False), ("muse-spark-1.3", True)]
    assert "no answer within" in looks[0].note


async def test_after_a_glimmer_failure_spark_looks_at_once(tmp_path: Path) -> None:
    spark = FakeLLM([look(), composed(alert=False, spoken="All clear.")] * 2)
    svc, _, _ = assistant(tmp_path, glimmer=SlowLLM(), spark=spark, timeout_s=0.05)
    await svc.answer(Ask(image=jpeg(), question="Clear?"))

    started = time.perf_counter()
    second = await svc.answer(Ask(image=jpeg(), question="Clear?"))

    assert second.spoken == "All clear."
    assert second.looked_by.startswith("muse-spark-1.3")
    assert time.perf_counter() - started < 0.3  # did not wait for Glimmer again
    await asyncio.sleep(0.35)  # let the background Glimmer call end


async def test_push_to_talk_audio_becomes_the_question(tmp_path: Path) -> None:
    transcriber = FakeTranscriber(
        [{"speaker": "s0", "text": " How dense is it ahead? ", "lang": "en"}]
    )
    glimmer = FakeLLM([look()])
    spark = FakeLLM([composed(alert=False, spoken="Moderately busy ahead.")])
    svc, _, _ = assistant(
        tmp_path, glimmer=glimmer, spark=spark, transcriber=transcriber, glimmer_up=True
    )

    answer = await svc.answer(Ask(image=jpeg(), mode="crowd", audio=b"RIFF", lang="es"))

    assert answer.heard == "How dense is it ahead?"
    assert answer.question == "How dense is it ahead?"
    assert answer.kind == "question"
    assert answer.spoken == "Moderately busy ahead."
    assert transcriber.calls[0]["lang_bias"] == ["es"]
    assert "dense" in transcriber.calls[0]["keywords"]  # type: ignore[operator]
    assert answer.stages[0].name == "hear"


async def test_unheard_audio_asks_again(tmp_path: Path) -> None:
    spark = FakeLLM([])
    svc, _, _ = assistant(
        tmp_path, glimmer=FakeLLM([]), spark=spark, transcriber=FakeTranscriber([])
    )

    answer = await svc.answer(Ask(image=jpeg(), audio=b"RIFF"))

    assert answer.spoken == NOT_HEARD
    assert answer.heard == ""
    assert spark.calls == []


async def test_spark_failure_falls_back_to_the_first_look(tmp_path: Path) -> None:
    svc, _, _ = assistant(tmp_path, glimmer=FakeLLM([look()]), spark=FakeLLM([]))

    answer = await svc.answer(Ask(image=jpeg(), question="What is ahead?"))

    assert answer.spoken == "A forklift is ahead. (first look only, not checked)"
    compose = next(s for s in answer.stages if s.name == "compose")
    assert not compose.ok


async def test_only_jpeg_or_png(tmp_path: Path) -> None:
    svc, _, _ = assistant(tmp_path, glimmer=None, spark=FakeLLM([]))
    with pytest.raises(WearablesError):
        await svc.answer(Ask(image=b"GIF89a", content_type="image/gif"))


async def test_aisle_question_shows_spark_the_confirmed_gaps(tmp_path: Path) -> None:
    shelf_spark = FakeLLM(
        [
            {
                "gaps": [
                    {
                        "box": [100, 100, 600, 600],
                        "section": "left bay, shelf 2",
                        "neighbours": "sourdough loaves",
                        "facings": 4,
                    }
                ]
            }
        ]
    )
    files = Files()
    sam = Sam({})
    spark = FakeLLM([composed(alert=False, spoken="One gap, left shelf two, by the sourdough.")])
    svc = PovAssistant(
        spark=spark,
        files=files,
        sam=sam,
        out_dir=tmp_path,
        clock=FakeClock(),
        glimmer=FakeLLM([look(count=["bread loaf"])]),
        glimmer_model=GLIMMER,
        shelf=ShelfScanner(spark=shelf_spark, files=files, sam=sam, out_dir=tmp_path / "frames"),
    )
    svc.mark_glimmer(up=True)

    answer = await svc.answer(Ask(image=jpeg(), mode="aisle", question="Any gaps?"))

    assert [(g.number, g.section, g.facings) for g in answer.gaps] == [(1, "left bay, shelf 2", 4)]
    assert answer.marked is not None
    assert (svc.frames_dir / answer.marked).is_file()
    assert sorted(sam.calls) == ["bread loaf", "product package"]
    content = spark.calls[0]["messages"][1]["content"]  # type: ignore[index]
    assert content[2] == {"type": "input_text", "text": GAPS_CAPTION}
    assert content[3] == {"type": "input_image", "file_id": answer.marked}
    shelf_stage = next(s for s in answer.stages if s.name == "shelf")
    assert shelf_stage.note == "1 gaps confirmed, 0 rejected by SAM 3.1"


async def test_a_cold_glimmer_races_spark_and_wins_when_quick(tmp_path: Path) -> None:
    glimmer = FakeLLM([look(alert=True, hazards=["forklift"], count=["forklift"])])
    spark = FakeLLM([look(), composed()])
    svc, _, _ = assistant(tmp_path, glimmer=glimmer, spark=spark, sam=Sam({"forklift": 1}))

    answer = await svc.answer(Ask(image=jpeg(), question="Safe?"))

    assert answer.looked_by == GLIMMER
    assert answer.verified
    looks = [(s.model, s.ok) for s in answer.stages if s.name == "look"]
    assert sorted(looks) == [(GLIMMER, True), ("muse-spark-1.3", True)]
    assert svc.glimmer_state == "up"


async def test_a_cold_slow_glimmer_does_not_hold_the_answer(tmp_path: Path) -> None:
    spark = FakeLLM([look(), composed(alert=False, spoken="Clear.")])
    svc, _, _ = assistant(tmp_path, glimmer=SlowLLM(), spark=spark)
    started = time.perf_counter()
    answer = await svc.answer(Ask(image=jpeg(), question="Clear?"))
    assert answer.spoken == "Clear."
    assert answer.looked_by.startswith("muse-spark-1.3")
    assert time.perf_counter() - started < 2.0  # not the 5 s Glimmer timeout
    assert svc.glimmer_state == "down"


async def test_warm_pings_glimmer_once_a_minute(tmp_path: Path) -> None:
    glimmer = FakeLLM([{"ok": True}])
    svc, _, _ = assistant(tmp_path, glimmer=glimmer, spark=FakeLLM([]))
    assert str(svc.glimmer_state) == "unknown"
    assert svc.warm() == "checking"
    await asyncio.sleep(0.05)
    assert svc.glimmer_state == "up"
    assert svc.warm() == "up"  # no second ping within the minute
    assert len(glimmer.calls) == 1
    assert glimmer.calls[0]["schema"] == "GlimmerPing"


async def test_a_failed_ping_makes_spark_look_at_once(tmp_path: Path) -> None:
    spark = FakeLLM([look(), composed(alert=False, spoken="Clear.")])
    svc, _, _ = assistant(tmp_path, glimmer=SlowLLM(), spark=spark, timeout_s=0.05)
    svc.warm()
    await asyncio.sleep(0.1)
    assert svc.glimmer_state == "down"
    started = time.perf_counter()
    answer = await svc.answer(Ask(image=jpeg(), question="Clear?"))
    assert answer.spoken == "Clear."
    assert time.perf_counter() - started < 0.3
    await asyncio.sleep(0.35)
    no_glimmer, _, _ = assistant(tmp_path, glimmer=None, spark=FakeLLM([]))
    assert no_glimmer.warm() == "none"
