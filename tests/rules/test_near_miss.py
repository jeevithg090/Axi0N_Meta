"""Near-miss metrics: closest approach and time-to-collision (P1-07)."""

from __future__ import annotations

import pytest

from rulecam.contracts import Track
from rulecam.rules import evaluate
from rulecam.rules.near_miss import (
    closest_approach,
    footprint_kinematics,
    near_miss_at,
    point_kinematics,
    time_to_collision,
)
from tests.rules.helpers import CAL, at, make_rule, walk_track

CONTACT = 0.5


def approach(
    start_x: float = 5.0, speed: float = 2.0, lateral: float = 0.0, seconds: float = 3
) -> list[Track]:
    """A forklift driving straight at a standing person along x."""
    steps = round(seconds * 10)
    forklift = [(start_x - speed * i / 10, lateral) for i in range(steps)]
    return [
        walk_track("cam1:person:1", [(0.0, 0.0)] * steps),
        walk_track("cam1:forklift:1", forklift, concept="forklift"),
    ]


def test_head_on_kinematics() -> None:
    assert time_to_collision((5.0, 0.0), (-2.0, 0.0), CONTACT) == pytest.approx(2.25)
    assert closest_approach((5.0, 0.0), (-2.0, 0.0), 1.0) == pytest.approx((1.0, 3.0))
    assert closest_approach((5.0, 0.0), (-2.0, 0.0), 5.0) == pytest.approx((2.5, 0.0))


def test_passing_wide_never_collides() -> None:
    assert time_to_collision((5.0, 1.0), (-2.0, 0.0), CONTACT) is None
    assert closest_approach((5.0, 1.0), (-2.0, 0.0), 3.0) == pytest.approx((2.5, 1.0))


def test_moving_apart_or_still() -> None:
    assert time_to_collision((5.0, 0.0), (2.0, 0.0), CONTACT) is None
    assert time_to_collision((5.0, 0.0), (0.0, 0.0), CONTACT) is None
    assert closest_approach((5.0, 0.0), (2.0, 0.0)) == (0.0, 5.0)
    assert time_to_collision((0.3, 0.0), (2.0, 0.0), CONTACT) == 0.0


def test_ttc_on_a_straight_line_approach_matches_analytic() -> None:
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "forklift", "lt": 3.0},
        concepts={"person": "person", "forklift": "forklift"},
    )
    events = evaluate(rule, approach(), calibration=None, zones={}, t0=at(0), t1=at(3))
    event = events[0]
    assert event.ts_start == at(1.1)  # first sample closer than 3 m: 5 - 2 * 1.1 = 2.8 m
    analytic_ttc = (2.8 - CONTACT) / 2.0
    ttc = event.predicate_trace["distance_m.ttc_s"]
    assert isinstance(ttc, float)
    assert abs(ttc - analytic_ttc) < 0.05
    assert event.predicate_trace["distance_m.separation_m"] == pytest.approx(2.8)
    assert event.predicate_trace["distance_m.closest_m"] == pytest.approx(0.8)
    assert event.predicate_trace["distance_m.closest_in_s"] == pytest.approx(1.0)


def test_ttc_with_both_objects_moving() -> None:
    steps = 30
    person = walk_track("cam1:person:1", [(0.1 * i / 10, 0.0) for i in range(steps)])
    forklift = walk_track(
        "cam1:forklift:1", [(6.0 - 1.9 * i / 10, 0.0) for i in range(steps)], concept="forklift"
    )
    metrics = near_miss_at(
        [person, forklift],
        camera_id="cam1",
        subject_concept="person",
        other_concept="forklift",
        at=at(1.0),
        calibration=None,
    )
    gap = (6.0 - 1.9) - 0.1  # 4.0 m apart at t = 1 s, closing at 2.0 m/s
    ttc = metrics["cam1:person:1"].ttc_s
    assert ttc is not None
    assert abs(ttc - (gap - CONTACT) / 2.0) < 0.05


def test_wide_pass_reports_no_collision_course() -> None:
    metrics = near_miss_at(
        approach(lateral=1.0),
        camera_id="cam1",
        subject_concept="person",
        other_concept="forklift",
        at=at(1.0),
        calibration=None,
    )
    assert metrics["cam1:person:1"].ttc_s is None


def test_unmeasurable_subjects_are_skipped() -> None:
    tracks = [walk_track("cam1:person:1", [(0.0, 0.0)])]  # one point: no velocity
    metrics = near_miss_at(
        tracks,
        camera_id="cam1",
        subject_concept="person",
        other_concept="forklift",
        at=at(0.0),
        calibration=None,
    )
    assert metrics == {}


# --- footprint geometry (WS1d): the metrics measure what the predicate measures ----------------


def test_a_degenerate_footprint_matches_the_point_formulas() -> None:
    for rel, vel in [((5.0, 0.0), (-2.0, 0.0)), ((5.0, 1.0), (-2.0, 0.0)), ((0.3, 0.0), (2, 0))]:
        assert footprint_kinematics(rel, rel, vel) == pytest.approx(point_kinematics(rel, vel))
        assert footprint_kinematics(rel, rel, vel, 5.0)[3] == time_to_collision(rel, vel, CONTACT)


def test_head_on_at_a_wide_footprint() -> None:
    """A 2 m wide bottom edge 5 m ahead, closing at 2 m/s: contact 0.5 m off the edge."""
    separation, closest, when, ttc = footprint_kinematics((5.0, -1.0), (5.0, 1.0), (-2.0, 0.0))
    assert separation == pytest.approx(5.0)
    assert (closest, when) == pytest.approx((3.0, 1.0))
    assert ttc == pytest.approx((5.0 - CONTACT) / 2.0)


def test_sliding_along_a_footprint_reports_the_earliest_closest_time() -> None:
    """Sliding past a 4 m edge 1 m to the side: 1 m from t = 0.5 s to 2.5 s; report 0.5 s."""
    _, closest, when, ttc = footprint_kinematics((1.0, 1.0), (5.0, 1.0), (-2.0, 0.0), 3.0)
    assert closest == pytest.approx(1.0)
    assert when == pytest.approx(0.5, abs=1e-4)
    assert ttc is None
    assert footprint_kinematics((1.0, 1.0), (5.0, 1.0), (0.0, 0.0))[2] == 0.0


def test_inside_the_contact_distance_is_a_collision_now() -> None:
    assert footprint_kinematics((0.2, -1.0), (0.2, 1.0), (1.0, 0.0))[3] == 0.0


def test_separation_equals_the_predicate_value_with_a_calibration() -> None:
    """Live WS1d: traces showed foot-to-foot metrics next to a footprint distance."""
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0},
        concepts={"person": "person", "forklift": "forklift"},
    )
    steps = 30
    wide = walk_track(
        "cam1:forklift:1",
        [(5.0 - 2.0 * i / 10, 1.5) for i in range(steps)],
        concept="forklift",
        size_px=(300.0, 120.0),  # a 3 m wide footprint: its end is nearer than its middle
    )
    person = walk_track("cam1:person:1", [(0.0, 0.0)] * steps)
    (event,) = evaluate(rule, [wide, person], calibration=CAL, zones={}, t0=at(0), t1=at(3))
    trace = event.predicate_trace
    assert trace["distance_m.separation_m"] == trace["distance_m.value"]
    value = trace["distance_m.value"]
    assert isinstance(value, float)
    assert value < 2.0
    closest = trace["distance_m.closest_m"]
    assert isinstance(closest, float)
    assert closest <= value


def test_an_object_seen_once_counts_as_standing_still() -> None:
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0},
        concepts={"person": "person", "forklift": "forklift"},
    )
    person = walk_track("cam1:person:1", [(0.0, 0.0)] * 20)
    flash = walk_track("cam1:forklift:1", [(1.0, 0.0)], concept="forklift", start_s=1.0)
    (event,) = evaluate(rule, [person, flash], calibration=None, zones={}, t0=at(0), t1=at(2))
    assert event.predicate_trace["distance_m.separation_m"] == pytest.approx(1.0)
    assert event.predicate_trace["distance_m.ttc_s"] == "none"
