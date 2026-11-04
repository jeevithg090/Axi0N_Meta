"""Tests for the glasses assistant: parallel looks, the first line, counts, the checked line."""

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
    AGREE,
    BOXES_CAPTION,
    GAPS_CAPTION,
    LOOKING,
    NOT_CHECKED,
    NOT_HEARD,
    Ask,
    PovAssistant,
    Voice,
    WearablesError,
    concepts,
)
from rulecam.wearables.schemas import Line, SparkLook, Stage
from tests.fakes import EPOCH, FakeClock, FakeLLM, FakeTranscriber, FakeTTS

GLIMMER = "muse-glimmer-30b"
SPARK = "muse-spark-1.3"


def jpeg() -> bytes:
    ok, data = cv2.imencode(".jpg", np.full((120, 160, 3), 128, dtype=np.uint8))
    assert ok
    return data.tobytes()


def look(
    *, alert: bool = False, answer: str = "A forklift is ahead.", hazards: list[str] | None = None
) -> dict[str, Any]:
    """Glimmer's look."""
    return {"scene": "A racked aisle.", "answer": answer, "hazards": hazards or [], "alert": alert}


def spark_look(
    *, alert: bool = False, spoken: str = "Stop, forklift ahead.", count: list[str] | None = None
) -> dict[str, Any]:
    """Spark's own look."""
    return {
        "spoken": spoken,
        "alert": alert,
        "severity": "danger" if alert else "none",
        "hazards": [{"what": "forklift", "where": "ahead", "severity": "danger"}] if alert else [],
        "count": count or [],
        "confidence": 0.8,
    }


def composed(
    *, spoken: str = "One forklift ahead.", rejected: list[str] | None = None
) -> dict[str, Any]:
    """Spark's check of counts or gaps."""
    return {
        "spoken": spoken,
        "detail": "SAM 3.1 boxed one forklift.",
        "alert": False,
        "severity": "info",
        "hazards": [],
        "rejected": rejected or [],
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


class Slow:
    """Answers after a delay (in the worker thread), or fails after it."""

    def __init__(self, inner: FakeLLM | None, delay_s: float) -> None:
        self.inner = inner
        self.delay_s = delay_s

    def structured(
        self, *, model: str, messages: list[dict[str, object]], schema: type[BaseModel], **_: Any
    ) -> BaseModel:
        time.sleep(self.delay_s)
        if self.inner is None:
            raise AssertionError("too late")
        return self.inner.structured(model=model, messages=messages, schema=schema)


def assistant(
    tmp_path: Path,
    *,
    glimmer: Any,
    spark: Any,
    sam: Sam | None = None,
    transcriber: FakeTranscriber | None = None,
    shelf: ShelfScanner | None = None,
    files: Files | None = None,
    timeout_s: float = 5.0,
    glimmer_up: bool = True,
) -> tuple[PovAssistant, Files, FakeTTS]:
    files, tts = files or Files(), FakeTTS()
    svc = PovAssistant(
        spark=spark,
        files=files,
        sam=sam or Sam({}),
        out_dir=tmp_path,
        clock=FakeClock(),
        glimmer=glimmer,
        glimmer_model=GLIMMER,
        shelf=shelf,
        transcriber=transcriber,
        voice=Voice(tts, "test voice"),
        glimmer_timeout_s=timeout_s,
        glimmer_grace_s=0.05,
    )
    if glimmer_up:
        svc.mark_glimmer(up=True)
    return svc, files, tts


def test_concepts_are_cleaned_deduplicated_and_capped() -> None:
    picked = SparkLook.model_validate(
        spark_look(count=[" Forklift. ", "forklift", "", "person  in lane", "pallet", "rack"])
    )
    assert concepts(picked) == ["forklift", "person in lane", "pallet"]
    assert concepts(None) == []


async def test_first_line_comes_from_the_quicker_look_then_both_agree(tmp_path: Path) -> None:
    glimmer = Slow(FakeLLM([look(alert=True, hazards=["forklift ahead"])]), 0.1)
    spark = FakeLLM([spark_look(alert=True)])
    sam = Sam({})
    svc, files, tts = assistant(tmp_path, glimmer=glimmer, spark=spark, sam=sam)
    firsts: list[Line] = []

    answer = await svc.answer(
        Ask(image=jpeg(), mode="warehouse", question="Safe to walk?", on_first=firsts.append)
    )

    assert answer.first is not None
    assert (answer.first.text, answer.first.by) == ("Stop, forklift ahead.", SPARK)
    assert answer.first.audio is not None
    assert (svc.audio_dir / answer.first.audio).is_file()
    assert firsts == [answer.first]
    assert answer.spoken == AGREE
    assert answer.verified
    assert answer.verified_by == [SPARK, GLIMMER]
    assert answer.looked_by == f"{GLIMMER} + {SPARK}"
    assert sam.calls == []  # no count was needed
    assert [t for t, _ in tts.calls] == ["Stop, forklift ahead.", AGREE]
    assert sorted(s.name for s in answer.stages) == ["look", "look", "speak", "speak first"]
    assert files.attached == [answer.frame]
    text = spark.calls[0]["messages"][1]["content"][0]["text"]  # type: ignore[index]
    assert "Safe to walk?" in text


async def test_a_count_question_runs_sam_and_spark_rejects_a_label_that_does_not_fit(
    tmp_path: Path,
) -> None:
    glimmer = FakeLLM([look(answer="One forklift, its driver in a hard hat.")])
    spark = FakeLLM(
        [
            spark_look(spoken="One forklift ahead.", count=["forklift", "hard hat"]),
            composed(
                spoken="One forklift; no hard hat seen.", rejected=["1 hard hat: a warning light"]
            ),
        ]
    )
    sam = Sam({"forklift": 1, "hard hat": 1})
    svc, _, _ = assistant(tmp_path, glimmer=glimmer, spark=spark, sam=sam)

    answer = await svc.answer(Ask(image=jpeg(), question="How many forklifts, any hard hats?"))

    assert sorted(sam.calls) == ["forklift", "hard hat"]
    assert answer.spoken == "One forklift; no hard hat seen."
    assert answer.rejected == ["1 hard hat: a warning light"]
    assert answer.marked is not None
    assert (svc.frames_dir / answer.marked).is_file()
    content = spark.calls[1]["messages"][1]["content"]  # type: ignore[index]
    assert content[1] == {"type": "input_text", "text": BOXES_CAPTION}
    assert content[2] == {"type": "input_image", "file_id": answer.marked}
    assert '"hard hat"' in content[0]["text"]
    assert "check" in [s.name for s in answer.stages]


async def test_a_quiet_patrol_frame_is_silent(tmp_path: Path) -> None:
    spark = FakeLLM([spark_look(spoken="")])
    svc, _, tts = assistant(tmp_path, glimmer=FakeLLM([look(answer="")]), spark=spark)

    answer = await svc.answer(Ask(image=jpeg(), mode="warehouse"))

    assert answer.kind == "patrol"
    assert answer.first is None
    assert answer.spoken == ""
    assert answer.audio is None
    assert tts.calls == []


async def test_a_patrol_alert_is_spoken_only_when_both_models_flag_it(tmp_path: Path) -> None:
    spark = FakeLLM([spark_look(alert=True, spoken="Spill ahead left.")])
    svc, _, _ = assistant(tmp_path, glimmer=FakeLLM([look(alert=True)]), spark=spark)
    answer = await svc.answer(Ask(image=jpeg(), mode="aisle"))
    assert answer.verified
    assert answer.first is None
    assert answer.spoken == "Spill ahead left."


async def test_a_patrol_alert_glimmer_did_not_see_is_shown_not_spoken(tmp_path: Path) -> None:
    spark = FakeLLM([spark_look(alert=True)])
    svc, _, _ = assistant(tmp_path, glimmer=Slow(None, 0.3), spark=spark, timeout_s=0.05)

    answer = await svc.answer(Ask(image=jpeg(), mode="warehouse"))

    assert answer.alert
    assert not answer.verified
    assert answer.spoken == ""
    looks = sorted((s.model, s.ok) for s in answer.stages if s.name == "look")
    assert looks == [(GLIMMER, False), (SPARK, True)]
    assert svc.glimmer_state == "down"


async def test_when_the_models_disagree_the_second_look_is_said(tmp_path: Path) -> None:
    glimmer = Slow(FakeLLM([look(alert=True, answer="Forklift reversing on your left.")]), 0.1)
    spark = FakeLLM([spark_look(alert=False, spoken="The aisle looks clear.")])
    svc, _, _ = assistant(tmp_path, glimmer=glimmer, spark=spark)

    answer = await svc.answer(Ask(image=jpeg(), question="Clear?"))

    assert answer.first is not None
    assert answer.first.text == "The aisle looks clear."
    assert answer.spoken == "Second look: Forklift reversing on your left."
    assert not answer.verified


async def test_a_cold_glimmer_does_not_hold_the_checked_line(tmp_path: Path) -> None:
    spark = FakeLLM([spark_look(spoken="Clear.")])
    svc, _, _ = assistant(tmp_path, glimmer=Slow(None, 0.4), spark=spark, glimmer_up=False)

    started = time.perf_counter()
    answer = await svc.answer(Ask(image=jpeg(), question="Clear?"))

    assert time.perf_counter() - started < 0.35  # Glimmer is left to finish in the background
    assert answer.first is not None
    assert answer.first.by == SPARK
    assert answer.looked_by == SPARK
    await asyncio.sleep(0.5)
    assert svc.glimmer_state == "down"


async def test_push_to_talk_audio_becomes_the_question(tmp_path: Path) -> None:
    transcriber = FakeTranscriber(
        [{"speaker": "s0", "text": " How dense is it ahead? ", "lang": "en"}]
    )
    spark = FakeLLM([spark_look(spoken="Moderately busy ahead.")])
    svc, files, _ = assistant(
        tmp_path, glimmer=Slow(FakeLLM([look()]), 0.1), spark=spark, transcriber=transcriber
    )

    answer = await svc.answer(Ask(image=jpeg(), mode="crowd", audio=b"RIFF", lang="es"))

    assert answer.heard == "How dense is it ahead?"
    assert answer.question == "How dense is it ahead?"
    assert answer.kind == "question"
    assert answer.first is not None
    assert answer.first.text == "Moderately busy ahead."
    assert transcriber.calls[0]["lang_bias"] == ["es"]
    assert "dense" in transcriber.calls[0]["keywords"]  # type: ignore[operator]
    assert answer.stages[0].name == "hear"
    assert files.attached == [answer.frame]  # uploaded while the question was being heard


async def test_unheard_audio_asks_again(tmp_path: Path) -> None:
    spark = FakeLLM([])
    svc, _, _ = assistant(
        tmp_path, glimmer=FakeLLM([]), spark=spark, transcriber=FakeTranscriber([])
    )
    answer = await svc.answer(Ask(image=jpeg(), audio=b"RIFF"))
    assert answer.spoken == NOT_HEARD
    assert answer.heard == ""
    assert spark.calls == []


async def test_when_both_looks_fail_say_so(tmp_path: Path) -> None:
    svc, _, _ = assistant(tmp_path, glimmer=FakeLLM([]), spark=FakeLLM([]))
    answer = await svc.answer(Ask(image=jpeg(), question="What is ahead?"))
    assert answer.first is None
    assert answer.spoken == NOT_CHECKED


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
    spark = FakeLLM(
        [spark_look(spoken="Shelf two looks thin."), composed(spoken="One gap, shelf two.")]
    )
    svc, _, _ = assistant(
        tmp_path,
        glimmer=Slow(FakeLLM([look()]), 0.1),
        spark=spark,
        sam=sam,
        files=files,
        shelf=ShelfScanner(spark=shelf_spark, files=files, sam=sam, out_dir=tmp_path / "frames"),
    )

    answer = await svc.answer(Ask(image=jpeg(), mode="aisle", question="Any gaps?"))

    assert [(g.number, g.section, g.facings) for g in answer.gaps] == [(1, "left bay, shelf 2", 4)]
    assert answer.spoken == "One gap, shelf two."
    assert answer.marked is not None
    assert sam.calls == ["product package"]  # the shelf check only; no count was asked
    content = spark.calls[1]["messages"][1]["content"]  # type: ignore[index]
    assert content[1] == {"type": "input_image", "file_id": answer.frame}
    assert content[2] == {"type": "input_text", "text": GAPS_CAPTION}
    assert content[3] == {"type": "input_image", "file_id": answer.marked}


async def test_appearance_never_reaches_the_ear(tmp_path: Path) -> None:
    glimmer = FakeLLM([look(answer="The driver in a red cap is reversing toward you.")])
    spark = Slow(FakeLLM([spark_look(spoken="")]), 0.1)
    svc, _, tts = assistant(tmp_path, glimmer=glimmer, spark=spark)

    answer = await svc.answer(Ask(image=jpeg(), question="What is happening?"))

    assert answer.first is not None
    assert answer.first.text == "The driver is reversing toward you."
    assert tts.calls[0][0] == "The driver is reversing toward you."
    assert answer.privacy_edits == 1


async def test_fixed_phrases_are_spoken_once_and_reused(tmp_path: Path) -> None:
    glimmer = Slow(FakeLLM([look(alert=True)]), 0.1)
    svc, _, tts = assistant(tmp_path, glimmer=glimmer, spark=FakeLLM([spark_look(alert=True)]))
    svc.prepare_phrases()
    assert svc.phrases["looking"] == "phrase-looking.wav"
    assert (svc.audio_dir / "phrase-agree.wav").is_file()
    before = len(tts.calls)

    answer = await svc.answer(Ask(image=jpeg(), question="Safe?"))

    assert answer.spoken == AGREE
    assert answer.audio == "phrase-agree.wav"
    assert [t for t, _ in tts.calls[before:]] == ["Stop, forklift ahead."]  # only the first line
    assert LOOKING in [t for t, _ in tts.calls[:before]]


async def test_progress_hears_each_stage_as_it_ends(tmp_path: Path) -> None:
    glimmer = Slow(FakeLLM([look()]), 0.05)
    svc, _, _ = assistant(tmp_path, glimmer=glimmer, spark=FakeLLM([spark_look(spoken="Clear.")]))
    seen: list[Stage] = []

    answer = await svc.answer(Ask(image=jpeg(), question="Clear?", progress=seen.append))

    assert seen == answer.stages
    assert seen[0].name == "look"


async def test_warm_pings_glimmer_once_a_minute(tmp_path: Path) -> None:
    glimmer = FakeLLM([{"ok": True}])
    svc, _, _ = assistant(tmp_path, glimmer=glimmer, spark=FakeLLM([]), glimmer_up=False)
    assert str(svc.glimmer_state) == "unknown"
    assert svc.warm() == "checking"
    await asyncio.sleep(0.05)
    assert svc.glimmer_state == "up"
    assert svc.warm() == "up"  # no second ping within the minute
    assert len(glimmer.calls) == 1
    assert glimmer.calls[0]["schema"] == "GlimmerPing"
    no_glimmer, _, _ = assistant(tmp_path, glimmer=None, spark=FakeLLM([]))
    assert no_glimmer.warm() == "none"
