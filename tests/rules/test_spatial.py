"""Atomic spatial and scene-level predicates (P1-06 step 3)."""

from __future__ import annotations

import numpy as np
import pytest

from rulecam.contracts import Calibration, CandidateEvent, RuleIR, Track, Zone
from rulecam.rules import RuleEvaluationError, evaluate
from rulecam.rules.geometry import rolling_median
from tests.rules.helpers import (
    CAL,
    INSIDE_M,
    OUTSIDE_M,
    SINK_ZONE,
    at,
    box_track,
    make_rule,
    walk_track,
    wrists,
)

PEOPLE_FORKLIFTS = {"person": "person", "forklift": "forklift"}


def run(
    rule: RuleIR,
    tracks: list[Track],
    *,
    calibration: Calibration | None = CAL,
    zones: dict[str, Zone] | None = None,
    seconds: float = 5.0,
) -> list[CandidateEvent]:
    return evaluate(
        rule, tracks, calibration=calibration, zones=zones or {}, t0=at(0), t1=at(seconds)
    )


# --- distance_m -------------------------------------------------------------------------------

NEAR = make_rule(
    {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0}, concepts=PEOPLE_FORKLIFTS
)


@pytest.mark.parametrize(
    ("person_m", "fires"),
    [((0.0, 1.95), True), ((0.0, 2.05), False), ((1.0, 1.7), True), ((2.0, 1.0), False)],
)
def test_distance_threshold_on_calibrated_tracks(
    person_m: tuple[float, float], fires: bool
) -> None:
    person = walk_track("cam1:person:1", [person_m] * 20, with_foot_m=False)
    forklift = walk_track("cam1:forklift:1", [(0.0, 0.0)] * 20, concept="forklift")
    events = run(NEAR, [person, forklift])
    assert bool(events) is fires
    if fires:
        assert events[0].measurement is not None
        assert events[0].measurement.unit == "m"
        assert events[0].measurement.value < 2.0


def test_distance_measures_to_the_footprint_edge_not_the_centre() -> None:
    # Forklift box is 40 px (0.4 m) wide: its footprint edge is 0.2 m from its foot point.
    person = walk_track("cam1:person:1", [(1.0, 0.0)] * 20, with_foot_m=False)
    forklift = walk_track("cam1:forklift:1", [(0.0, 0.0)] * 20, concept="forklift")
    events = run(NEAR, [person, forklift])
    assert events[0].measurement is not None
    assert events[0].measurement.value == pytest.approx(0.8)


def test_distance_to_foot_point_reference() -> None:
    rule = make_rule(
        {"op": "distance_m", "a": "person.foot_point", "b": "forklift.foot_point", "lt": 0.9},
        concepts=PEOPLE_FORKLIFTS,
    )
    person = walk_track("cam1:person:1", [(1.0, 0.0)] * 20)
    forklift = walk_track("cam1:forklift:1", [(0.0, 0.0)] * 20, concept="forklift")
    assert run(rule, [person, forklift]) == []


def test_distance_without_calibration_uses_foot_m() -> None:
    person = walk_track("cam1:person:1", [(0.0, 1.5)] * 20)
    forklift = walk_track("cam1:forklift:1", [(0.0, 0.0)] * 20, concept="forklift")
    events = run(NEAR, [person, forklift], calibration=None)
    assert events[0].measurement is not None
    assert events[0].measurement.value == pytest.approx(1.5)


def test_unmeasurable_distance_never_fires() -> None:
    person = walk_track("cam1:person:1", [(0.0, 1.0)] * 20, with_foot_m=False)
    forklift = walk_track("cam1:forklift:1", [(0.0, 0.0)] * 20, concept="forklift")
    assert run(NEAR, [person, forklift], calibration=None) == []
    gt = make_rule({"op": "distance_m", "a": "person", "b": "forklift", "gt": 3.0})
    assert run(gt, [person, forklift], calibration=None) == []


def test_distance_gt_with_nobody_around_is_true() -> None:
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "staff", "gt": 3.0},
        concepts={"person": "person", "staff": "staff uniform"},
    )
    events = run(rule, [walk_track("cam1:person:1", [(0.0, 0.0)] * 20)])
    assert len(events) == 1
    assert events[0].measurement is None  # infinitely far is not a measurement
    assert events[0].predicate_trace["distance_m.value"] == "inf"


def test_distance_between_people_excludes_self() -> None:
    rule = make_rule({"op": "distance_m", "a": "person", "b": "person", "lt": 1.0})
    alone = walk_track("cam1:person:1", [(0.0, 0.0)] * 20)
    assert run(rule, [alone]) == []
    other = walk_track("cam1:person:2", [(0.5, 0.0)] * 20)
    assert len(run(rule, [alone, other])) == 2


def test_distance_needs_a_bound() -> None:
    rule = make_rule({"op": "distance_m", "a": "person", "b": "forklift"})
    with pytest.raises(RuleEvaluationError):
        run(rule, [walk_track("cam1:person:1", [(0.0, 0.0)] * 5)])


def test_concepts_match_by_prompt() -> None:
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "bucket", "lt": 2.0},
        concepts={"person": "person", "bucket": "excavator bucket"},
    )
    person = walk_track("cam1:person:1", [(0.0, 1.0)] * 20)
    bucket = walk_track("cam1:bucket:1", [(0.0, 0.0)] * 20, concept="excavator bucket")
    assert len(run(rule, [person, bucket])) == 1


# --- inside -----------------------------------------------------------------------------------

INSIDE = make_rule({"op": "inside", "object": "person", "zone": "sink_zone"})


def test_inside_polygon_uses_the_foot_point() -> None:
    inside = walk_track("cam1:person:1", [INSIDE_M] * 20)
    outside = walk_track("cam1:person:2", [OUTSIDE_M] * 20)
    events = run(INSIDE, [inside, outside], zones={"sink_zone": SINK_ZONE})
    assert [e.predicate_trace["subject"] for e in events] == ["cam1:person:1"]


def test_zone_on_another_camera_is_never_entered() -> None:
    person = walk_track("cam2:person:1", [INSIDE_M] * 20, camera_id="cam2")
    assert run(INSIDE, [person], zones={"sink_zone": SINK_ZONE}) == []


def test_zone_falls_back_to_rule_polygon_and_zone_id() -> None:
    rule = make_rule(
        {"op": "inside", "object": "person", "zone": "line"},
        zones={
            "line": {"kind": "polygon", "camera_id": "cam1", "polygon_px": SINK_ZONE.polygon_px}
        },
    )
    person = walk_track("cam1:person:1", [INSIDE_M] * 20)
    assert len(run(rule, [person])) == 1
    by_id = {"whatever": SINK_ZONE}
    assert len(run(INSIDE, [person], zones=by_id)) == 1


def test_unknown_or_incomplete_zone_raises() -> None:
    person = walk_track("cam1:person:1", [INSIDE_M] * 5)
    with pytest.raises(RuleEvaluationError, match="unknown zone"):
        run(INSIDE, [person])
    line = Zone(zone_id="sink_zone", camera_id="cam1", kind="polygon", polygon_px=((0, 0), (1, 1)))
    with pytest.raises(RuleEvaluationError, match="vertices"):
        run(INSIDE, [person], zones={"sink_zone": line})
    empty = Zone(zone_id="sink_zone", camera_id="cam1", kind="buffer")
    with pytest.raises(RuleEvaluationError, match="neither"):
        run(INSIDE, [person], zones={"sink_zone": empty})


@pytest.mark.parametrize(("distance_m", "fires"), [(0.5, True), (0.7, False)])
def test_metric_buffer_zone_around_a_concept(distance_m: float, fires: bool) -> None:
    rule = make_rule(
        {"op": "inside", "object": "person", "zone": "sink_zone"},
        concepts={"person": "person", "sink": "sink"},
        zones={
            "sink_zone": {
                "kind": "buffer",
                "camera_id": "cam1",
                "buffer_of": "sink",
                "buffer_m": 0.6,
            }
        },
    )
    sink = walk_track("cam1:sink:1", [(0.0, 0.0)] * 20, concept="sink", size_px=(0.0, 50.0))
    person = walk_track("cam1:person:1", [(0.0, distance_m)] * 20)
    assert bool(run(rule, [sink, person])) is fires


# --- overlap ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("min_iou", "fires"), [(0.0, True), (0.3, True), (0.5, False)])
def test_overlap_iou(min_iou: float, fires: bool) -> None:
    rule = make_rule(
        {"op": "overlap", "a": "hands", "b": "faucet", "min_iou": min_iou},
        concepts={"hands": "hands", "faucet": "faucet"},
    )
    hands = box_track("cam1:hands:1", [(0, 0, 100, 100)] * 20, concept="hands")
    faucet = box_track("cam1:faucet:1", [(50, 0, 150, 100)] * 20, concept="faucet")  # IoU 1/3
    assert bool(run(rule, [hands, faucet])) is fires


def test_overlap_without_contact_is_false() -> None:
    rule = make_rule({"op": "overlap", "a": "hands", "b": "faucet"})
    hands = box_track("cam1:hands:1", [(0, 0, 10, 10)] * 20, concept="hands")
    faucet = box_track("cam1:faucet:1", [(50, 50, 60, 60)] * 20, concept="faucet")
    assert run(rule, [hands, faucet]) == []


# --- without ----------------------------------------------------------------------------------

PERSON_BOX = (0.0, 0.0, 40.0, 160.0)


def without_rule(region: str) -> RuleIR:
    return make_rule(
        {"op": "without", "subject": "person", "item": "hard hat", "region": region},
        concepts={"person": "person", "hard hat": "hard hat"},
    )


@pytest.mark.parametrize(
    ("hat_box", "region", "fires"),
    [
        ((10.0, 5.0, 30.0, 25.0), "top_25pct", False),  # hat on the head
        ((10.0, 70.0, 30.0, 90.0), "top_25pct", True),  # hat held at the waist
        ((10.0, 70.0, 30.0, 90.0), "full", False),
        ((100.0, 5.0, 120.0, 25.0), "full", True),  # someone else's hat
    ],
)
def test_without_regions(
    hat_box: tuple[float, float, float, float], region: str, fires: bool
) -> None:
    person = box_track("cam1:person:1", [PERSON_BOX] * 20, concept="person")
    hat = box_track("cam1:hat:1", [hat_box] * 20, concept="hard hat")
    events = run(without_rule(region), [person, hat])
    assert bool(events) is fires


def test_without_with_no_item_at_all_is_true() -> None:
    person = box_track("cam1:person:1", [PERSON_BOX] * 20, concept="person")
    events = run(without_rule("top_25pct"), [person])
    assert events[0].measurement is not None
    assert (events[0].measurement.value, events[0].measurement.unit) == (0.0, "count")


@pytest.mark.parametrize(
    ("glove_centre", "pose", "fires"),
    [
        ((20.0, 120.0), False, False),  # lower half of the box, no pose
        ((20.0, 40.0), False, True),  # upper half, no pose
        ((35.0, 90.0), True, False),  # within reach of the right wrist
        ((20.0, 120.0), True, True),  # lower half, but far from both wrists
    ],
)
def test_without_hands_region(glove_centre: tuple[float, float], pose: bool, fires: bool) -> None:
    rule = make_rule(
        {"op": "without", "subject": "person", "item": "gloves", "region": "hands"},
        concepts={"person": "person", "gloves": "gloves"},
    )
    keypoints = wrists((5.0, 90.0), (35.0, 92.0)) if pose else None
    person = box_track("cam1:person:1", [PERSON_BOX] * 20, concept="person", keypoints=keypoints)
    x, y = glove_centre
    glove = box_track("cam1:glove:1", [(x - 3, y - 3, x + 3, y + 3)] * 20, concept="gloves")
    assert bool(run(rule, [person, glove])) is fires


# --- moving -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("speed", "fires"), [(1.0, True), (0.3, False), (0.0, False)])
def test_moving_speed(speed: float, fires: bool) -> None:
    rule = make_rule({"op": "moving", "object": "forklift", "speed_mps_gt": 0.5})
    path = [(speed * i / 10, 0.0) for i in range(30)]
    forklift = walk_track("cam1:forklift:1", path, concept="forklift")
    events = run(rule, [forklift])
    assert bool(events) is fires
    if fires:
        assert events[0].measurement is not None
        assert events[0].measurement.value == pytest.approx(speed)
        assert events[0].measurement.unit == "m/s"


def test_rolling_median_ignores_gaps_and_spikes() -> None:
    values = np.array([1.0, 1.0, 50.0, 1.0, 1.0, np.nan, np.nan, np.nan, np.nan, np.nan])
    smooth = rolling_median(values, 5)
    assert smooth[2] == 1.0
    assert np.isnan(smooth[-1])
    assert smooth[4] == 1.0


# --- count and state --------------------------------------------------------------------------


def test_count_in_zone() -> None:
    rule = make_rule({"op": "count", "object": "box", "zone": "sink_zone", "gt": 2, "lt": 5})
    boxes = [walk_track(f"cam1:box:{i}", [INSIDE_M] * 20, concept="box") for i in range(3)]
    outside = walk_track("cam1:box:9", [OUTSIDE_M] * 20, concept="box")
    events = run(rule, [*boxes, outside], zones={"sink_zone": SINK_ZONE})
    assert len(events) == 1
    assert events[0].predicate_trace["subject"] == "scene"
    assert events[0].measurement is not None
    assert events[0].measurement.value == 3.0
    assert run(rule, boxes[:2], zones={"sink_zone": SINK_ZONE}) == []


def test_count_without_bounds_means_any() -> None:
    rule = make_rule({"op": "count", "object": "box"})
    assert run(rule, [walk_track("cam1:box:1", [OUTSIDE_M] * 20, concept="box")])


@pytest.mark.parametrize("concept", ["open door", "door/open"])
def test_state_is_presence_of_the_state_concept(concept: str) -> None:
    rule = make_rule({"op": "state", "object": "door", "state": "open"})
    door = walk_track("cam1:door:1", [INSIDE_M] * 20, concept=concept)
    assert len(run(rule, [door])) == 1
    closed = walk_track("cam1:door:2", [INSIDE_M] * 20, concept="closed door")
    assert run(rule, [closed]) == []


def test_state_present_means_the_object_itself() -> None:
    rule = make_rule({"op": "state", "object": "pallet", "state": "present"})
    assert run(rule, [walk_track("cam1:pallet:1", [INSIDE_M] * 20, concept="pallet")])
