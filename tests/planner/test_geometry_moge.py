"""Calibration from MoGe-2 in the planner (add-on 3.5): survey, people cross-check, fallback."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from rulecam.contracts import Detection
from rulecam.contracts.core import BoxXYXY, Calibration
from rulecam.contracts.plan import RequestItem
from rulecam.perception.calibration import MOGE_REFERENCE
from rulecam.perception.masks import encode_rle
from rulecam.perception.point_map import PointMap
from rulecam.planner.geometry import (
    FLOOR_PROMPT,
    Sighting,
    model_calibration,
    model_calibrations,
    scene_calibrations,
)
from rulecam.planner.pipeline import PipelineInput, PlannerPipeline, PlanStore, SurveyResult
from tests.perception.test_point_map_calibration import synthetic_point_map
from tests.planner.geometry_fakes import HEIGHT, WIDTH, SceneSam, detection, write_frames
from tests.planner.test_geometry import FORKLIFT
from tests.planner.test_geometry_pipeline import _spark
from tests.planner.test_pipeline import FakeAttacher

K = np.array([[500.0, 0.0, WIDTH / 2], [0.0, 500.0, HEIGHT / 2], [0.0, 0.0, 1.0]])


def point_map() -> PointMap:
    points = synthetic_point_map(
        height_m=3.0, pitch_deg=25.0, rows=36, cols=48, width=WIDTH, height=HEIGHT, intrinsics=K
    )
    return PointMap(model="fake-moge", width=WIDTH, height=HEIGHT, intrinsics_px=K, points=points)


class Moge:
    def __init__(self, *, available: bool = True, fail: bool = False) -> None:
        self._available = available
        self.fail = fail
        self.cameras: list[str] = []

    def available(self) -> bool:
        return self._available

    def estimate(self, image: bytes, *, camera_id: str) -> PointMap:
        self.cameras.append(camera_id)
        if self.fail:
            raise RuntimeError("cuda out of memory")
        return point_map()


def floor_detection(camera_id: str) -> Detection:
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[HEIGHT // 3 :] = 1
    return detection(
        camera_id, FLOOR_PROMPT, (0.0, HEIGHT / 3, WIDTH, HEIGHT), rle=encode_rle(mask)
    )


class FloorSam(SceneSam):
    """The geometry fakes' scene, plus a floor over the lower two thirds of the frame."""

    def __init__(self, *, floor: bool = True, pallet: bool = False) -> None:
        super().__init__(pallet=pallet)
        self._floor = floor

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        if concept == FLOOR_PROMPT:
            return [floor_detection(camera_id)] if self._floor else []
        return super().segment_image(frame, concept, camera_id=camera_id)


def test_the_survey_calibrates_from_moge_when_no_reference_object_is_in_view(
    tmp_path: Path,
) -> None:
    store = PlanStore(tmp_path / "plans")
    moge = Moge()
    pipeline = PlannerPipeline(
        spark=_spark(), files=FakeAttacher(), segmenter=FloorSam(), store=store, geometry=moge
    )
    plan = pipeline.run(
        PipelineInput(
            plan_id="dock-plan",
            site_name="Dock 3",
            request=(RequestItem(kind="text", text=FORKLIFT),),
            camera_frames={"dock": write_frames(tmp_path, "dock")},
        )
    )
    survey = store.load_step("dock-plan", "survey", SurveyResult)
    assert survey is not None
    assert survey.calibrations["dock"].reference == MOGE_REFERENCE
    assert moge.cameras == ["dock"]  # once per camera
    (rule,) = plan.rules
    assert rule.calibration == "ground-plane@dock"
    calibration = next(c for c in plan.acceptance.checks if c.check == "calibration_err<=tol")
    assert calibration.passed


def test_without_moge_the_survey_keeps_the_old_fallbacks(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "plans")
    moge = Moge(available=False)
    pipeline = PlannerPipeline(
        spark=_spark(), files=FakeAttacher(), segmenter=FloorSam(), store=store, geometry=moge
    )
    pipeline.run(
        PipelineInput(
            plan_id="dock-plan",
            site_name="Dock 3",
            request=(RequestItem(kind="text", text=FORKLIFT),),
            camera_frames={"dock": write_frames(tmp_path, "dock")},
        )
    )
    assert moge.cameras == []
    assert store.calibrations("dock-plan") == {}


def test_people_who_disagree_with_the_model_drop_it(tmp_path: Path) -> None:
    frames = {"dock": write_frames(tmp_path, "dock", n=2)}
    tiny: list[BoxXYXY] = [(100.0 + 40 * i, 400.0, 104.0 + 40 * i, 410.0) for i in range(3)]
    kept = model_calibrations(
        frames, geometry=Moge(), segmenter=FloorSam(), seed=1, people={"dock": ()}
    )
    assert kept["dock"].reference == MOGE_REFERENCE
    dropped = model_calibrations(
        frames, geometry=Moge(), segmenter=FloorSam(), seed=1, people={"dock": tiny}
    )
    assert dropped == {}


def test_no_floor_or_a_failing_model_gives_no_model_calibration(tmp_path: Path) -> None:
    (frame,) = write_frames(tmp_path, "dock", n=1)
    no_floor = FloorSam(floor=False)
    assert model_calibration("dock", frame, geometry=Moge(), segmenter=no_floor) is None
    broken = Moge(fail=True)
    assert model_calibration("dock", frame, geometry=broken, segmenter=FloorSam()) is None


def _cal(reference: str, err_m: float) -> Calibration:
    return Calibration(
        camera_id="dock",
        homography=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        method="reference_object",
        reference=reference,
        err_m=err_m,
    )


def test_a_model_calibration_competes_on_error_and_beats_the_person_guess(
    tmp_path: Path,
) -> None:
    (frame,) = write_frames(tmp_path, "dock", n=1)
    people = [
        Sighting("dock", frame, "person", (detection("dock", "person", box),))
        for box in (
            (100.0, 100.0, 150.0, 300.0),
            (300.0, 150.0, 330.0, 260.0),
            (400.0, 60.0, 480.0, 400.0),
        )
    ]
    model = {"dock": _cal(MOGE_REFERENCE, 0.08)}
    assert scene_calibrations(people, model=model)["dock"].reference == MOGE_REFERENCE
    assert scene_calibrations(people)["dock"].reference == "person_height_1.7m"
