"""Reference probes: metric rules get a scene calibration whatever Spark called the pallets (P2-06).

Live finding: on the warehouse camera the survey named the pallets "stacked pallet with bags",
which `reference_kind` rightly refuses, so no calibration was made and the forklift 2 m rule
stayed amber. The probe step now prompts SAM with the reference phrases itself when a concern
needs metres.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.contracts import Detection
from rulecam.contracts.plan import Probe, RequestItem, SitePlan
from rulecam.perception.calibration import calibrate_reference
from rulecam.perception.masks import decode_rle
from rulecam.planner.geometry import (
    REFERENCE_PROBE_FRAMES,
    REFERENCE_PROMPTS,
    ReferenceProbe,
    metric_cameras,
    needs_metres,
    reference_kind,
)
from rulecam.planner.pipeline import (
    PipelineInput,
    PlannerPipeline,
    PlanStore,
    ProbeResult,
    SurveyResult,
    probe_summary,
)
from rulecam.planner.steps.probe import ConceptNeeds
from rulecam.planner.steps.survey import DEFAULT_SURVEY_FRAMES, ProposedConcepts, sample_frames
from tests.planner.geometry_fakes import SceneSam, pallet_detection, write_frames
from tests.planner.test_geometry import FORKLIFT
from tests.planner.test_geometry_pipeline import WASH, _spark
from tests.planner.test_pipeline import FakeAttacher, SchemaLLM

LOADED = "stacked pallet with bags"


class WarehouseSam(SceneSam):
    """The live warehouse: loaded pallets, and a bare pallet SAM finds when asked for one."""

    def __init__(self) -> None:
        super().__init__(pallet=True)
        self.log: list[tuple[str, str, Path]] = []

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        self.log.append((concept, camera_id, frame))
        if concept == LOADED:
            loaded = pallet_detection(camera_id)
            return [loaded.model_copy(update={"concept": LOADED})]
        return super().segment_image(
            frame, concept, camera_id=camera_id, include_confidence=include_confidence
        )

    def reference_calls(self) -> list[tuple[str, str, Path]]:
        return [call for call in self.log if call[0] in REFERENCE_PROMPTS]


def _warehouse_spark(
    concern: str = FORKLIFT, concepts: tuple[str, ...] = ("person", "forklift")
) -> SchemaLLM:
    spark = _spark()
    spark._answers["ProposedConcepts"] = ProposedConcepts(concepts=["forklift", "person", LOADED])
    spark._answers["ConceptNeeds"] = ConceptNeeds.model_validate(
        {"needs": [{"concern": concern, "concepts": list(concepts)}]}
    )
    return spark


def _run(
    tmp_path: Path,
    sam: WarehouseSam,
    *,
    concern: str = FORKLIFT,
    concepts: tuple[str, ...] = ("person", "forklift"),
    cameras: tuple[str, ...] = ("dock",),
    owner: dict[str, object] | None = None,
) -> tuple[SitePlan, PlanStore, PipelineInput]:
    store = PlanStore(tmp_path / "plans")
    pipeline = PlannerPipeline(
        spark=_warehouse_spark(concern, concepts),
        files=FakeAttacher(),
        segmenter=sam,
        store=store,
    )
    inputs = PipelineInput.model_validate(
        {
            "plan_id": "dock-plan",
            "site_name": "Dock 3",
            "request": (RequestItem(kind="text", text=concern),),
            "camera_frames": {cam: write_frames(tmp_path, cam) for cam in cameras},
            "calibrations": owner or {},
        }
    )
    return pipeline.run(inputs), store, inputs


def test_metric_concern_calibrates_from_a_probed_pallet_the_survey_did_not_name(
    tmp_path: Path,
) -> None:
    sam = WarehouseSam()
    plan, store, inputs = _run(tmp_path, sam)

    survey = store.load_step("dock-plan", "survey", SurveyResult)
    probe = store.load_step("dock-plan", "probe", ProbeResult)
    assert survey is not None
    assert probe is not None
    assert [i.concept for i in survey.inventory if "pallet" in i.concept] == [LOADED]
    assert survey.calibrations == {}  # the survey's wording gives no reference
    assert probe.calibrations["dock"].reference == "pallet_48x40in"
    assert probe.reference_probes == tuple(
        ReferenceProbe(camera_id="dock", prompt=p, n_frames=REFERENCE_PROBE_FRAMES, n_detections=n)
        for p, n in (("wooden pallet", REFERENCE_PROBE_FRAMES), ("1 m floor square", 0))
    )
    assert store.calibrations("dock-plan")["dock"] == probe.calibrations["dock"]
    (rule,) = plan.rules
    assert rule.calibration == "ground-plane@dock"
    calibration_check = [c for c in plan.acceptance.checks if c.check == "calibration_err<=tol"]
    assert [c.passed for c in calibration_check] == [True]

    # Bounded: a few of the survey's own frames per reference prompt.
    survey_frames = set(
        sample_frames(inputs.camera_frames["dock"], DEFAULT_SURVEY_FRAMES, seed=inputs.seed)
    )
    calls = sam.reference_calls()
    assert len(calls) == REFERENCE_PROBE_FRAMES * len(REFERENCE_PROMPTS)
    assert {frame for _, _, frame in calls} <= survey_frames
    assert "references on dock: wooden pallet (3 found in 3 frames)" in probe_summary(probe)
    assert "calibrated from pallet_48x40in" in probe_summary(probe)


def test_no_metric_concern_spends_no_reference_calls(tmp_path: Path) -> None:
    sam = WarehouseSam()
    _, store, _ = _run(tmp_path, sam, concern=WASH, concepts=("sink", "person"))

    probe = store.load_step("dock-plan", "probe", ProbeResult)
    assert probe is not None
    assert sam.reference_calls() == []
    assert probe.reference_probes == ()
    assert probe.calibrations == {}
    assert "references" not in probe_summary(probe)


def test_owner_calibrated_camera_is_not_probed(tmp_path: Path) -> None:
    mask = decode_rle(pallet_detection("yard").mask.rle)
    owner = calibrate_reference("yard", mask, "pallet_48x40in").model_copy(
        update={"method": "clicks4", "reference": None}
    )
    sam = WarehouseSam()
    plan, store, _ = _run(tmp_path, sam, cameras=("dock", "yard"), owner={"yard": owner})

    assert {cam for _, cam, _ in sam.reference_calls()} == {"dock"}
    probe = store.load_step("dock-plan", "probe", ProbeResult)
    assert probe is not None
    assert set(probe.calibrations) == {"dock"}
    assert store.calibrations("dock-plan")["yard"].method == "clicks4"
    (rule,) = plan.rules
    # The gauge checks the concern on dock (its evidence camera), so the rule runs and is
    # calibrated there only (its camera scope, P2-06c).
    assert rule.calibration == "ground-plane@dock"


def test_no_reference_found_means_no_calibration(tmp_path: Path) -> None:
    class BarePallets(WarehouseSam):
        def segment_image(
            self,
            frame: Path,
            concept: str,
            *,
            camera_id: str = "probe",
            include_confidence: bool = True,
        ) -> list[Detection]:
            if concept in REFERENCE_PROMPTS:
                self.log.append((concept, camera_id, frame))
                return []
            return super().segment_image(frame, concept, camera_id=camera_id)

    sam = BarePallets()
    plan, store, _ = _run(tmp_path, sam)

    probe = store.load_step("dock-plan", "probe", ProbeResult)
    assert probe is not None
    assert len(sam.reference_calls()) == REFERENCE_PROBE_FRAMES * len(REFERENCE_PROMPTS)
    assert probe.calibrations == {}
    assert store.calibrations("dock-plan") == {}
    assert probe_summary(probe).endswith("; no calibration")
    assert [c.passed for c in plan.acceptance.checks if c.check == "calibration_err<=tol"] == [
        False
    ]


@pytest.mark.parametrize(
    "text",
    [
        FORKLIFT,
        "keep 2m clear of the dock edge",
        "nobody within 1.5 metres of the press",
        "forklift speed over 8 km/h in the aisle",
        "mantener 2 metros de distancia",
        "stay six feet from the conveyor",
    ],
)
def test_needs_metres_spots_distances_and_speeds(text: str) -> None:
    assert needs_metres(text)


@pytest.mark.parametrize(
    "text", [WASH, "wear gloves at the prep counter", "fire exit kept clear", "wait 5 min"]
)
def test_needs_metres_ignores_concerns_without_metres(text: str) -> None:
    assert not needs_metres(text)


def test_metric_cameras_are_those_that_saw_a_needed_concept_and_are_uncalibrated() -> None:
    needs = {FORKLIFT: ("forklift", "person"), WASH: ("sink",)}
    seen = {"a": ["forklift"], "b": ["sink"], "c": ["person"], "d": []}

    assert metric_cameras(needs, seen) == ("a", "c")
    assert metric_cameras(needs, seen, calibrated=["c"]) == ("a",)
    assert metric_cameras({WASH: ("sink",)}, seen) == ()


def test_reference_prompts_name_their_reference_kinds() -> None:
    assert {p: reference_kind(p) for p in REFERENCE_PROMPTS} == REFERENCE_PROMPTS
    assert reference_kind(LOADED) is None


def test_probe_summary_without_references() -> None:
    probe = ProbeResult(
        needs={},
        probes=(
            Probe(
                concept="forklift",
                camera_id="dock",
                n_frames=1,
                detect_rate=1.0,
                detect_lb=0.2,
                median_px_height=180.0,
            ),
        ),
    )
    assert probe_summary(probe) == "1 probes"
