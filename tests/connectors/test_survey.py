"""The first look: Spark describes, SAM 3.1 detects, Spark matches (fakes, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from rulecam.clients.errors import ModelHTTPError
from rulecam.connectors.survey import (
    CameraFit,
    CameraLook,
    CameraSurvey,
    CameraSurveyor,
    UseCaseFit,
    clean_concepts,
    concept_result,
)
from rulecam.contracts import Detection, Mask
from tests.fakes import EPOCH, FakeClock, FakeLLM


class Attacher:
    def __init__(self) -> None:
        self.attached: list[Path] = []

    def attach(self, path: Path) -> dict[str, object]:
        self.attached.append(path)
        return {"type": "input_image", "file_id": "file-1"}


class ImageSam:
    def __init__(self, found: dict[str, int], fail: str | None = None) -> None:
        self.found = found
        self.fail = fail
        self.asked: list[str] = []

    def segment_image(
        self, frame: Path, concept: str, *, camera_id: str = "", include_confidence: bool = True
    ) -> list[Detection]:
        self.asked.append(concept)
        if concept == self.fail:
            msg = "SAM down"
            raise ModelHTTPError(msg, status=503, attempts=1)
        return [
            Detection(
                camera_id=camera_id,
                frame_idx=0,
                ts=EPOCH,
                concept=concept,
                object_id=i,
                mask=Mask(rle="", box_xyxy=(10.0 * i, 10.0, 10.0 * i + 40, 50.0), confidence=0.8),
            )
            for i in range(self.found.get(concept, 0))
        ]


def snapshot(tmp_path: Path) -> Path:
    path = tmp_path / "cam-1_000000.jpg"
    cv2.imwrite(str(path), np.full((120, 160, 3), 128, dtype=np.uint8))
    return path


LOOK = CameraLook(
    name="Checkout lanes 1-3",
    description="High corner view over three checkout lanes.",
    viewpoint="high corner",
    setting="supermarket checkout",
    concepts=["Person", "person ", "shopping cart", "checkout lane"],
)
FIT = CameraFit(
    use_cases=[
        UseCaseFit(
            use_case="supermarket",
            fit="strong",
            why="SAM finds 3 people and 2 carts at the lanes.",
            first_rules=["Open a lane when more than 4 people queue"],
        ),
        UseCaseFit(use_case="warehouse", fit="poor", why="No forklifts."),
    ],
    placement_tip="Tilt down to see the end of the queue.",
)


def surveyor(tmp_path: Path, llm: FakeLLM, sam: ImageSam) -> CameraSurveyor:
    return CameraSurveyor(
        spark=llm, files=Attacher(), sam=sam, clock=FakeClock(), out_dir=tmp_path / "surveys"
    )


def test_three_steps_each_see_the_last(tmp_path: Path) -> None:
    llm = FakeLLM([LOOK, FIT])
    sam = ImageSam({"person": 3, "shopping cart": 2})
    steps: list[CameraSurvey] = []
    survey = surveyor(tmp_path, llm, sam).run(
        "cam-1", snapshot(tmp_path), chunk_id="cam-1_000000", on_step=steps.append
    )
    assert survey.status == "done"
    assert [s.status for s in steps] == ["describing", "detecting", "matching", "done"]
    assert sam.asked == ["person", "shopping cart", "checkout lane"]
    by_concept = {d.concept: d for d in survey.detections}
    assert by_concept["person"].count == 3
    assert by_concept["person"].max_confidence == pytest.approx(0.8)
    assert by_concept["checkout lane"].count == 0
    assert survey.fit == FIT
    assert survey.marked is not None
    assert (tmp_path / "surveys" / survey.marked).is_file()
    assert set(survey.timings) == {"describe_s", "detect_s", "match_s", "total_s"}
    match_input = llm.calls[1]["messages"][1]["content"][0]["text"]  # type: ignore[index]
    evidence = json.loads(match_input)
    assert evidence["spark_saw"]["name"] == "Checkout lanes 1-3"
    assert {r["concept"]: r["count"] for r in evidence["sam_found"]}["shopping cart"] == 2


def test_private_space_stops_before_detection(tmp_path: Path) -> None:
    look = LOOK.model_copy(update={"private_space": "staff break room"})
    sam = ImageSam({})
    survey = surveyor(tmp_path, FakeLLM([look]), sam).run("cam-1", snapshot(tmp_path))
    assert survey.status == "blocked"
    assert "break room" in (survey.error or "")
    assert sam.asked == []


def test_sam_errors_stay_per_concept(tmp_path: Path) -> None:
    sam = ImageSam({"person": 1}, fail="shopping cart")
    survey = surveyor(tmp_path, FakeLLM([LOOK, FIT]), sam).run("cam-1", snapshot(tmp_path))
    assert survey.status == "done"
    failed = next(d for d in survey.detections if d.concept == "shopping cart")
    assert failed.error == "SAM down"


def test_spark_failure_marks_the_survey_failed(tmp_path: Path) -> None:
    class Down(FakeLLM):
        def structured(self, **_: object) -> CameraLook:  # type: ignore[override]
            msg = "503 from Spark"
            raise ModelHTTPError(msg, status=503, attempts=1)

    survey = surveyor(tmp_path, Down(), ImageSam({})).run("cam-1", snapshot(tmp_path))
    assert survey.status == "failed"
    assert "503" in (survey.error or "")
    assert survey.finished_at == EPOCH


def test_helpers() -> None:
    assert clean_concepts(["A  Cart", "a cart", "", "x" * 61]) == ["a cart"]
    result = concept_result("cart", [], 100, 100, 0.123)
    assert (result.count, result.max_confidence, result.area_share, result.seconds) == (
        0,
        None,
        0.0,
        0.12,
    )
