"""Operator exclusion: a forklift's driver is not a pedestrian (WS1c, live finding 1c).

Live: "keep people >= 2 m from a moving forklift" fired with distance 0-0.8 m on a warehouse clip
whose only person was the driver. The engine now derives, per person-vehicle pair, whether the
person rides the vehicle (box containment >= 0.5 and co-moving over the window) and ignores that
vehicle for them.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from rulecam.contracts import CandidateEvent, RuleIR, Track
from rulecam.rules import OperatorExclusion, evaluate
from rulecam.rules.backtest import DecisionContext
from rulecam.rules.operators import (
    has_operator_pair,
    image_velocity,
    is_operator_pair,
    is_person_like,
    is_vehicle_like,
)
from tests.rules.helpers import CAL, at, make_rule, walk_track

CONCEPTS = {"person": "person", "forklift": "forklift"}
NEAR_MOVING: dict[str, object] = {
    "op": "all",
    "of": [
        {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0},
        {"op": "moving", "object": "forklift", "speed_mps_gt": 0.2},
    ],
}
RULE = make_rule(NEAR_MOVING, concepts=CONCEPTS, rule_id="forklift-proximity")
BUFFER_RULE = make_rule(
    {
        "op": "all",
        "of": [
            {"op": "inside", "object": "person", "zone": "fz"},
            {"op": "moving", "object": "forklift", "speed_mps_gt": 0.2},
        ],
    },
    concepts=CONCEPTS,
    zones={"fz": {"kind": "buffer", "camera_id": "cam1", "buffer_of": "forklift", "buffer_m": 2.0}},
)
STEPS = 60
SPEED = 1.0  # m/s along x
FORKLIFT_PX = (200.0, 240.0)  # 2.0 x 2.4 m box above its foot point
PERSON_PX = (60.0, 120.0)


def forklift(steps: int = STEPS) -> Track:
    path = [(2.0 + SPEED * i / 10, 5.0) for i in range(steps)]
    return walk_track("cam1:forklift:1", path, concept="forklift", size_px=FORKLIFT_PX)


def driver(steps: int = STEPS, *, off_after: int | None = None) -> Track:
    """Rides 0.5 m above the forklift's bottom edge (its box is inside the forklift's)."""
    path = []
    for i in range(steps):
        x = 2.0 + SPEED * i / 10
        if off_after is None or i < off_after:
            path.append((x, 4.5))
        else:
            path.append((2.0 + SPEED * off_after / 10 + 1.5, 5.5))
    return walk_track("cam1:person:1", path, size_px=PERSON_PX)


def pedestrian_beside(steps: int = STEPS) -> Track:
    """Walks with the forklift, 0.5 m beyond its footprint's end, outside its box."""
    path = [(2.0 + SPEED * i / 10 + 1.5, 5.0) for i in range(steps)]
    return walk_track("cam1:person:2", path, size_px=PERSON_PX)


def pedestrian_in_front(steps: int = STEPS) -> Track:
    """Stands 0.5 m in front of the passing forklift: inside its box, not moving with it."""
    return walk_track("cam1:person:3", [(5.0, 5.5)] * steps, size_px=PERSON_PX)


def run(rule: RuleIR, tracks: list[Track], **kwargs: Any) -> list[CandidateEvent]:
    return evaluate(rule, tracks, calibration=CAL, zones={}, t0=at(0), t1=at(STEPS / 10), **kwargs)


# --- the live finding ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule", [RULE, BUFFER_RULE], ids=["distance_m", "buffer"])
def test_the_driver_of_a_moving_forklift_does_not_fire(rule: RuleIR) -> None:
    assert run(rule, [forklift(), driver()]) == []


@pytest.mark.parametrize("rule", [RULE, BUFFER_RULE], ids=["distance_m", "buffer"])
def test_without_exclusion_the_driver_fires_as_live(rule: RuleIR) -> None:
    events = run(rule, [forklift(), driver()], operator_exclusion=False)
    assert len(events) == 1


@pytest.mark.parametrize("rule", [RULE, BUFFER_RULE], ids=["distance_m", "buffer"])
@pytest.mark.parametrize("person", [pedestrian_beside, pedestrian_in_front])
def test_a_pedestrian_at_the_same_distance_still_fires(
    rule: RuleIR, person: Callable[[], Track]
) -> None:
    track = person()
    events = run(rule, [forklift(), driver(), track])
    assert [e.predicate_trace["subject"] for e in events] == [track.track_id]


def test_a_driver_who_steps_off_is_a_pedestrian_again() -> None:
    events = run(RULE, [forklift(), driver(off_after=30)])
    assert len(events) == 1
    assert events[0].ts_start >= at(3.0)


def test_the_driver_still_counts_near_another_forklift() -> None:
    other = walk_track(
        "cam1:forklift:2",
        [(2.0 + SPEED * i / 10 + 1.5, 4.0) for i in range(STEPS)],
        concept="forklift",
    )
    events = run(RULE, [forklift(), driver(), other])
    assert len(events) == 1


def test_a_vehicle_subject_ignores_its_own_operator() -> None:
    rule = make_rule(
        {"op": "distance_m", "a": "forklift", "b": "person", "lt": 2.0}, concepts=CONCEPTS
    )
    assert run(rule, [forklift(), driver()]) == []
    assert len(run(rule, [forklift(), pedestrian_beside()])) == 1


def test_a_parked_forklift_never_links_its_driver() -> None:
    """Conservative side: no motion, no evidence of riding, so the person counts."""
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0}, concepts=CONCEPTS
    )
    parked = walk_track(
        "cam1:forklift:1", [(2.0, 5.0)] * STEPS, concept="forklift", size_px=FORKLIFT_PX
    )
    seated = walk_track("cam1:person:1", [(2.0, 4.5)] * STEPS, size_px=PERSON_PX)
    assert len(run(rule, [parked, seated])) == 1


def test_a_person_pair_is_never_excluded() -> None:
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "worker", "lt": 2.0},
        concepts={"person": "person", "worker": "worker"},
    )
    big = walk_track(
        "cam1:worker:1",
        [(2.0 + SPEED * i / 10, 5.0) for i in range(STEPS)],
        concept="worker",
        size_px=FORKLIFT_PX,
    )
    assert len(run(rule, [big, driver()])) == 1


def test_near_miss_metrics_leave_out_the_ridden_vehicle() -> None:
    late = walk_track(
        "cam1:forklift:2",
        [(2.0 + SPEED * (i + 10) / 10 + 1.5, 4.0) for i in range(STEPS - 10)],
        concept="forklift",
        start_s=1.0,
    )
    (event,) = run(RULE, [forklift(), driver(), late])
    trace = event.predicate_trace
    assert event.ts_start == at(1.0)
    separation = trace["all.0.distance_m.separation_m"]
    assert separation == trace["all.0.distance_m.value"]
    assert isinstance(separation, float)
    assert separation > 1.0  # the other forklift, not the ridden one


# --- override and re-derivation -----------------------------------------------------------------


def test_the_owner_override_is_keyed_by_rule_id() -> None:
    config = OperatorExclusion.from_csv(" forklift-proximity, ,other-rule ")
    assert config.disabled_rules == ("forklift-proximity", "other-rule")
    assert not config.enabled_for("forklift-proximity")
    assert config.enabled_for("wash-before-line")
    assert OperatorExclusion.from_csv(None).disabled_rules == ()
    events = run(RULE, [forklift(), driver()], operator_exclusion=config.enabled_for(RULE.id))
    assert len(events) == 1


def test_the_decision_context_records_the_flag() -> None:
    context = DecisionContext(rule=RULE, calibration=CAL, t0=at(0), t1=at(STEPS / 10))
    assert context.operator_exclusion is True
    assert context.evaluate([forklift(), driver()]) == []
    off = context.model_copy(update={"operator_exclusion": False})
    assert len(off.evaluate([forklift(), driver()])) == 1
    assert DecisionContext.model_validate_json(off.model_dump_json()) == off


# --- vocabulary and kinematics ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["person", "people", "warehouse worker", "pedestrians"])
def test_person_words(name: str) -> None:
    assert is_person_like(name)
    assert not is_vehicle_like(name)


@pytest.mark.parametrize(
    "name",
    ["forklift", "fork lift", "fork_lift", "pallet jack", "cart", "delivery truck", "vehicle"],
)
def test_vehicle_words(name: str) -> None:
    assert is_vehicle_like(name)
    assert not is_person_like(name)


def test_operator_pairs_are_found_in_distance_and_buffer_rules() -> None:
    assert has_operator_pair(RULE)
    assert has_operator_pair(BUFFER_RULE)
    assert is_operator_pair(RULE, "forklift", "person")
    assert not is_operator_pair(RULE, "person", "person")
    sink = make_rule(
        {"op": "distance_m", "a": "person", "b": "sink", "lt": 1.0},
        concepts={"person": "person", "sink": "sink"},
    )
    assert not has_operator_pair(sink)
    polygon = make_rule(
        {"op": "inside", "object": "person", "zone": "dock"},
        concepts=CONCEPTS,
        zones={
            "dock": {"kind": "polygon", "camera_id": "cam1", "polygon_px": [[0, 0], [1, 0], [1, 1]]}
        },
    )
    assert not has_operator_pair(polygon)


def test_image_velocity_is_a_centred_least_squares_slope() -> None:
    boxes = np.array([[10.0 * i, 0.0, 10.0 * i + 4, 8.0] for i in range(12)])
    boxes[5] = np.nan
    velocity = image_velocity(boxes, 10.0)
    assert velocity[3] == pytest.approx((100.0, 0.0))
    assert math.isnan(image_velocity(boxes[:1], 10.0)[0, 0])
