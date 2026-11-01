"""Planner geometry: calibration and zones from the scene, grounding compiled rules (P2-06)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from rulecam.contracts.core import Calibration, Detection, Zone
from rulecam.contracts.plan import FeasibilityVerdict, Probe
from rulecam.contracts.rule_ir import (
    ActionSpec,
    All,
    ConceptSpec,
    DistanceM,
    Inside,
    Moving,
    RuleIR,
    RuleSource,
    ZoneSpec,
)
from rulecam.perception.calibration import ground_distance_m, zone_in_view
from rulecam.planner.acceptance import (
    CALIBRATION_TOL_M,
    AcceptanceOutcome,
    PlannedRule,
    RepairLoop,
    SiteFacts,
)
from rulecam.planner.geometry import (
    RecordingSegmenter,
    Sighting,
    SiteGeometry,
    is_place,
    place_polygon,
    reference_kind,
    scene_calibrations,
    scene_zones,
    standing_people,
    trusted_calibrations,
    zone_slug,
)
from rulecam.planner.models import Concern
from rulecam.planner.steps.compile import compile_rule, rule_to_draft
from tests.planner.geometry_fakes import (
    HEIGHT,
    PALLET_M,
    SINK_BOX,
    WIDTH,
    SceneSam,
    detection,
    pallet_detection,
    to_image,
    write_frames,
)
from tests.planner.test_compile import MockSparkLLM

FORKLIFT = "keep people 2 m from a moving forklift"


def _sighting(
    camera_id: str, concept: str, *detections: Detection, frame: Path | None = None
) -> Sighting:
    return Sighting(camera_id, frame or Path("f.jpg"), concept, tuple(detections))


def forklift_buffer_rule(camera_id: str = "dock", *, polygon: bool = False) -> RuleIR:
    """`person inside a 2 m buffer of a moving forklift` (optionally with an invented polygon)."""
    spec = ZoneSpec(
        kind="buffer",
        camera_id=camera_id,
        buffer_of="forklift",
        buffer_m=2.0,
        polygon_px=((0.0, 0.0), (10.0, 0.0), (10.0, 10.0)) if polygon else None,
    )
    return RuleIR(
        id="forklift-buffer",
        source=RuleSource(kind="plan", plan_id="dock"),
        concepts={
            "person": ConceptSpec(prompt="person"),
            "forklift": ConceptSpec(prompt="forklift"),
        },
        zones={"forklift_zone": spec},
        predicate=All(
            of=(
                Inside(object="person", zone="forklift_zone"),
                Moving(object="forklift", speed_mps_gt=0.2),
            )
        ),
        severity="high",
        actions=(ActionSpec(kind="interlock_cut"), ActionSpec(kind="log")),
    )


# --- concepts -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("concept", "kind"),
    [
        ("wooden pallet", "pallet_48x40in"),
        ("Pallet", "pallet_48x40in"),
        ("pallet jack", None),
        ("pallet of boxes", None),
        ("pallet rack", None),
        ("1 m floor square", "floor_square_1m"),
        ("sink", None),
    ],
)
def test_reference_concepts(concept: str, kind: str | None) -> None:
    assert reference_kind(concept) == kind


def test_places_are_fixed_things_people_stand_at() -> None:
    assert all(map(is_place, ["sink", "prep counter", "fire exit door", "loading dock"]))
    assert not any(map(is_place, ["person", "forklift", "gloves", "hands"]))
    assert zone_slug("Prep  Counter") == "prep_counter"


# --- calibration from the scene -----------------------------------------------------------------


def test_a_pallet_detection_calibrates_its_camera_to_scale() -> None:
    sightings = [_sighting("dock", "wooden pallet", pallet_detection("dock"))]

    calibrations = scene_calibrations(sightings)

    cal = calibrations["dock"]
    assert (cal.method, cal.reference) == ("reference_object", "pallet_48x40in")
    assert cal.err_m < CALIBRATION_TOL_M
    a, b, c = (tuple(p) for p in to_image([PALLET_M[0], PALLET_M[1], PALLET_M[3]]))
    assert ground_distance_m(cal, a, b) == pytest.approx(1.219, rel=0.02)
    assert ground_distance_m(cal, a, c) == pytest.approx(1.016, rel=0.02)
    far = tuple(to_image([(2.0, 0.0)])[0])
    assert ground_distance_m(cal, a, far) == pytest.approx(2.0, rel=0.05)


def test_the_best_fit_per_camera_wins_and_boxes_never_calibrate() -> None:
    good = pallet_detection("dock")
    box_only = detection("dock", "wooden pallet", good.mask.box_xyxy)  # no mask: no footprint
    sightings = [
        _sighting("dock", "wooden pallet", box_only, good),
        _sighting("yard", "wooden pallet", box_only.model_copy(update={"camera_id": "yard"})),
        _sighting("dock", "pallet jack", good),
    ]
    calibrations = scene_calibrations(sightings)
    assert list(calibrations) == ["dock"]
    assert calibrations["dock"] == scene_calibrations([_sighting("dock", "pallet", good)])["dock"]


def test_a_door_that_disagrees_with_the_scale_rejects_the_calibration() -> None:
    pallet = pallet_detection("dock")
    tiny_door = detection("dock", "door", (150.0, 290.0, 300.0, 300.0))  # 150 px wide, 10 px tall
    assert scene_calibrations([_sighting("dock", "wooden pallet", pallet)])
    sightings = [_sighting("dock", "wooden pallet", pallet), _sighting("dock", "door", tiny_door)]
    assert scene_calibrations(sightings) == {}


def _people(camera_id: str, scale: float) -> list[Detection]:
    """Standing people at three depths, `scale` times the height a 1.7 m adult has there."""
    boxes = []
    for x_m, y_m in ((0.5, 0.5), (1.5, 2.0), (2.5, 3.5)):
        foot_x, foot_y = to_image([(x_m, y_m)])[0]
        a, b = to_image([(x_m - 0.25, y_m), (x_m + 0.25, y_m)])
        px_per_m = float(np.hypot(*(b - a))) / 0.5
        tall = 1.7 * px_per_m * scale
        half = max(tall / 4.0, 1.0)
        boxes.append((foot_x - half, foot_y - tall, foot_x + half, foot_y))
    return [detection(camera_id, "person", box) for box in boxes]


def test_people_who_disagree_with_a_pallet_reject_it_and_calibrate_the_camera(
    tmp_path: Path,
) -> None:
    (frame,) = write_frames(tmp_path, "dock", n=1)
    pallet = _sighting("dock", "wooden pallet", pallet_detection("dock"), frame=frame)
    agreeing = _sighting("dock", "person", *_people("dock", 1.0), frame=frame)
    tiny = _sighting("dock", "person", *_people("dock", 0.25), frame=frame)

    kept = scene_calibrations([pallet, agreeing])["dock"]
    assert kept.reference == "pallet_48x40in"

    replaced = scene_calibrations([pallet, tiny])["dock"]
    assert replaced.reference == "person_height_1.7m"
    assert replaced.err_m > kept.err_m


@pytest.mark.parametrize(
    ("concept", "counted"),
    [
        ("person", True),
        ("standing worker", True),
        ("warehouse workers", True),
        ("pedestrian", True),
        ("forklift driver", False),
        ("seated operator", False),
        ("worker hands", False),
        ("forklift", False),
    ],
)
def test_spark_phrases_for_standing_people(tmp_path: Path, concept: str, counted: bool) -> None:
    (frame,) = write_frames(tmp_path, "dock", n=1)
    people = _people("dock", 1.0)
    found = standing_people([_sighting("dock", concept, *people, frame=frame)])
    assert (found.get("dock", []) != []) is counted


def test_a_survey_calibration_the_probes_people_contradict_is_not_trusted(
    tmp_path: Path,
) -> None:
    (frame,) = write_frames(tmp_path, "dock", n=1)
    survey = scene_calibrations([_sighting("dock", "wooden pallet", pallet_detection("dock"))])
    agreeing = [_sighting("dock", "person", *_people("dock", 1.0), frame=frame)]
    tiny = [_sighting("dock", "person", *_people("dock", 0.25), frame=frame)]

    assert trusted_calibrations(survey, agreeing) == survey
    assert trusted_calibrations(survey, tiny) == {}
    assert trusted_calibrations(survey, []) == survey


def test_people_cut_by_the_frame_or_sitting_are_not_a_ruler(tmp_path: Path) -> None:
    (frame,) = write_frames(tmp_path, "dock", n=1)
    sitting = [
        detection(
            "dock", "person", (100.0 + 50 * i, 300.0 + 40 * i, 200.0 + 50 * i, 360.0 + 40 * i)
        )
        for i in range(3)
    ]
    cut = [
        detection("dock", "person", (100.0 + 50 * i, 0.0, 130.0 + 50 * i, 300.0)) for i in range(3)
    ]
    assert scene_calibrations([_sighting("dock", "person", *sitting, *cut, frame=frame)]) == {}


def test_the_recording_segmenter_keeps_every_call(tmp_path: Path) -> None:
    sam = SceneSam()
    recorder = RecordingSegmenter(sam)
    frame = tmp_path / "f.jpg"
    assert recorder.segment_image(frame, "sink", camera_id="k1")
    assert recorder.segment_image(frame, "thermometer", camera_id="k1") == []
    assert [(s.camera_id, s.concept, len(s.detections)) for s in recorder.sightings] == [
        ("k1", "sink", 1),
        ("k1", "thermometer", 0),
    ]


# --- zones from the scene -----------------------------------------------------------------------


def test_a_sink_detection_becomes_a_zone_in_view(tmp_path: Path) -> None:
    frame = write_frames(tmp_path, "k1", n=1)[0]
    weak = detection("k1", "sink", (0.0, 0.0, 20.0, 20.0), confidence=0.3)
    sightings = [
        _sighting("k1", "sink", detection("k1", "sink", SINK_BOX), weak, frame=frame),
        _sighting("k1", "person", detection("k1", "person", (0.0, 0.0, 5.0, 5.0)), frame=frame),
    ]

    zones = scene_zones(sightings, ["Sink", "person", "thermometer"])

    assert [(z.zone_id, z.camera_id, z.kind) for z in zones] == [("sink", "k1", "polygon")]
    polygon = zones[0].polygon_px
    assert polygon == ((285.0, 120.0), (415.0, 120.0), (415.0, 248.0), (285.0, 248.0))
    assert zone_in_view(zones[0], WIDTH, HEIGHT).state == "inside"


def test_place_zones_are_clipped_and_named_per_camera(tmp_path: Path) -> None:
    k1, k2 = write_frames(tmp_path, "k1", n=1)[0], write_frames(tmp_path, "k2", n=1)[0]
    edge = (600.0, 400.0, 640.0, 480.0)
    sightings = [
        _sighting("k1", "prep counter", detection("k1", "prep counter", edge), frame=k1),
        _sighting("k2", "prep counter", detection("k2", "prep counter", SINK_BOX), frame=k2),
        _sighting("k3", "prep counter", detection("k3", "prep counter", SINK_BOX)),  # unreadable
    ]
    zones = {z.zone_id: z for z in scene_zones(sightings, ["prep counter"])}
    assert sorted(zones) == ["prep_counter_k1", "prep_counter_k2"]
    assert zone_in_view(zones["prep_counter_k1"], WIDTH, HEIGHT).state == "inside"
    assert place_polygon((700.0, 0.0, 800.0, 10.0), WIDTH, HEIGHT) is None


# --- grounding compiled rules -------------------------------------------------------------------


CAL = Calibration(
    camera_id="dock",
    homography=((0.01, 0.0, 0.0), (0.0, 0.01, 0.0), (0.0, 0.0, 1.0)),
    method="reference_object",
    reference="pallet_48x40in",
    err_m=0.03,
)


def _geometry(*, calibrated: bool) -> SiteGeometry:
    sink = Zone(
        zone_id="sink", camera_id="dock", kind="polygon", polygon_px=((1, 1), (9, 1), (9, 9))
    )
    return SiteGeometry.build(
        ["dock"], scene_zones=[sink], scene_calibrations={"dock": CAL} if calibrated else {}
    )


def test_grounding_strips_invented_buffer_pixels_and_names_the_calibration() -> None:
    grounded = _geometry(calibrated=True).ground(forklift_buffer_rule(polygon=True))
    assert grounded.zones["forklift_zone"].polygon_px is None
    assert grounded.calibration == "ground-plane@dock"
    assert _geometry(calibrated=False).ground(forklift_buffer_rule()).calibration is None


def test_grounding_rejects_invented_polygons_and_repairs_candidate_ones() -> None:
    geometry = _geometry(calibrated=False)
    base = forklift_buffer_rule()
    invented = ZoneSpec(kind="polygon", camera_id="dock", polygon_px=((0, 0), (5, 0), (5, 5)))
    rule = base.model_copy(update={"zones": {**base.zones, "made_up": invented}})
    with pytest.raises(ValueError, match="'made_up' is a polygon Rulecam was not given"):
        geometry.ground(rule)
    repaired = geometry.ground(base.model_copy(update={"zones": {**base.zones, "sink": invented}}))
    assert repaired.zones["sink"] == geometry.zones["sink"]
    bad_buffer = base.zones["forklift_zone"].model_copy(update={"camera_id": "nowhere"})
    with pytest.raises(ValueError, match="on camera 'nowhere'"):
        geometry.ground(base.model_copy(update={"zones": {"forklift_zone": bad_buffer}}))
    zero = base.zones["forklift_zone"].model_copy(update={"buffer_m": 0.0})
    with pytest.raises(ValueError, match="buffer_m > 0"):
        geometry.ground(base.model_copy(update={"zones": {"forklift_zone": zero}}))


def test_owner_geometry_wins_over_the_scene() -> None:
    scene = Zone(
        zone_id="sink", camera_id="dock", kind="polygon", polygon_px=((1, 1), (9, 1), (9, 9))
    )
    owner = scene.model_copy(update={"polygon_px": ((2, 2), (8, 2), (8, 8))})
    clicks = CAL.model_copy(update={"method": "clicks4", "reference": None})
    geometry = SiteGeometry.build(
        ["dock"],
        owner_zones=[owner],
        scene_zones=[scene],
        owner_calibrations={"dock": clicks},
        scene_calibrations={"dock": CAL},
    )
    assert geometry.zones["sink"].polygon_px == owner.polygon_px
    assert geometry.zone_origins["sink"] == "drawn by the owner"
    assert geometry.calibrations["dock"].method == "clicks4"


def test_the_compiler_sees_real_zones_and_repairs_an_invented_polygon() -> None:
    invented = ZoneSpec(kind="polygon", camera_id="dock", polygon_px=((0, 0), (5, 0), (5, 5)))
    bad = forklift_buffer_rule()
    bad = bad.model_copy(update={"zones": {**bad.zones, "aisle": invented}})
    spark = MockSparkLLM([rule_to_draft(bad), rule_to_draft(forklift_buffer_rule(polygon=True))])

    rule = compile_rule(FORKLIFT, spark=spark, geometry=_geometry(calibrated=True))

    # Grounded (the invented polygon repaired), then canonicalised (WS1e): the buffer form of
    # "keep people 2 m from a moving forklift" becomes the one distance form, with no zone.
    assert rule.zones == {}
    assert rule.predicate == All(
        of=(
            DistanceM(a="person", b="forklift", lt=2.0),
            Moving(object="forklift", speed_mps_gt=0.2),
        )
    )
    assert rule.calibration == "ground-plane@dock"
    prompt = str(spark.compile_calls[0]["messages"])
    assert "Candidate zones: sink (polygon on dock, seen by the camera)" in prompt
    assert "dock (ground calibration: reference_object pallet_48x40in, err 0.03 m)" in prompt
    assert "'aisle' is a polygon Rulecam was not given" in str(spark.compile_calls[1]["messages"])
    uncalibrated = SiteGeometry.build(["dock"])
    assert "Candidate zones: none" in "\n".join(uncalibrated.prompt_lines())
    assert "dock (no ground calibration" in "\n".join(uncalibrated.prompt_lines())


# --- acceptance: calibrated vs not --------------------------------------------------------------


def _probes() -> tuple[Probe, ...]:
    return tuple(
        Probe(
            concept=c,
            camera_id="dock",
            n_frames=24,
            detect_rate=1.0,
            detect_lb=0.86,
            median_px_height=90,
        )
        for c in ("person", "forklift")
    )


def _accept(calibrations: dict[str, Calibration]) -> AcceptanceOutcome:
    concern = Concern(text=FORKLIFT, source_ref="text")
    verdict = FeasibilityVerdict(concern=FORKLIFT, verdict="observable", says="I can check this.")
    loop = RepairLoop(
        SiteFacts(views={"dock": (WIDTH, HEIGHT)}, probes=_probes(), calibrations=calibrations),
        recompile=lambda c, f: pytest.fail("nothing to repair"),
        probe=lambda concepts: (),
        verify_citation=lambda c: pytest.fail("no citation"),
    )
    return loop.run([PlannedRule(concern=concern, rule=forklift_buffer_rule())], [verdict])


def test_a_calibrated_forklift_buffer_rule_passes_its_checks() -> None:
    outcome = _accept({"dock": CAL})
    assert [r.id for r in outcome.rules] == ["forklift-buffer"]
    assert outcome.acceptance.all_passed
    assert outcome.feasibility[0].verdict == "observable"
    assert outcome.questions == ()


def test_without_calibration_the_rule_goes_amber_and_asks_for_a_known_distance() -> None:
    outcome = _accept({})
    assert [r.id for r in outcome.rules] == ["forklift-buffer"]
    failed = [c.check for c in outcome.acceptance.checks if not c.passed]
    assert failed == ["calibration_err<=tol"]
    assert outcome.feasibility[0].verdict == "caveat"
    assert "no ground calibration" in outcome.feasibility[0].says
    (question,) = outcome.questions
    assert question.target_field == "calibration"
    assert (question.requires_frame_tap, question.camera_id) == (True, "dock")
    assert "known distance" in question.text
    rough = _accept({"dock": CAL.model_copy(update={"err_m": 0.5})})
    assert not rough.acceptance.all_passed
    assert len(rough.questions) == 1
