"""Near-miss metrics: closest approach and time-to-collision (P1-07)."""

from __future__ import annotations

import pytest

from rulecam.contracts import Track
from rulecam.rules import evaluate
from rulecam.rules.near_miss import closest_approach, near_miss_at, time_to_collision
from tests.rules.helpers import at, make_rule, walk_track

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
