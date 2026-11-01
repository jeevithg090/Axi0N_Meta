"""Operator exclusion: a forklift's driver is not a pedestrian (WS1c, live findings 1c, P1-07).

Live: "keep people >= 2 m from a moving forklift" fired with distance 0-0.8 m on a warehouse clip
whose only person was the driver. The engine now derives, per person-vehicle pair, whether the
person rides the vehicle (box containment and co-moving over the window) and ignores that
vehicle for them. P1-07 (01 Nov, phone-23): a slow close-up forklift moved under the image-speed
gate while `moving(forklift, 0.2)` was true, so the seated driver was never linked; the real
tracks are `tests/fixtures/rules/live_driver_only.json`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from rulecam.contracts import Calibration, CandidateEvent, RuleIR, Track, TrackPoint
from rulecam.rules import OperatorExclusion, evaluate
from rulecam.rules.backtest import DecisionContext
from rulecam.rules.geometry import norm
from rulecam.rules.operators import (
    STILL_HEIGHTS_PER_S,
    has_operator_pair,
    image_velocity,
    is_operator_pair,
    is_person_like,
    is_vehicle_like,
    operator_mask,
    reshape_rate,
    slopes,
)
from rulecam.rules.timeline import Timeline, resample
from tests.rules.helpers import CAL, HZ, at, box_track, make_rule, walk_track

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


# --- slow and close-up vehicles (P1-07) ---------------------------------------------------------

SLOW = 0.25  # m/s: above `moving(forklift, 0.2)`
CLOSE_UP_PX = (400.0, 720.0)  # a box that fills the 720 px frame, as on phone-23
DISTANCE_ONLY = make_rule(
    {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0}, concepts=CONCEPTS
)


def slow_forklift(steps: int = STEPS, *, start_s: float = 0.0, **kwargs: Any) -> Track:
    path = [(2.0 + SLOW * i / 10, 5.0) for i in range(steps)]
    return walk_track(
        "cam1:forklift:1",
        path,
        concept="forklift",
        size_px=CLOSE_UP_PX,
        start_s=start_s,
        **kwargs,
    )


def slow_driver(steps: int = STEPS, *, start_s: float = 0.0, **kwargs: Any) -> Track:
    path = [(2.0 + SLOW * i / 10, 4.5) for i in range(steps)]
    return walk_track("cam1:person:1", path, size_px=PERSON_PX, start_s=start_s, **kwargs)


def mask(
    person: Track, vehicle: Track, *, calibration: Calibration | None = CAL, steps: int = STEPS
) -> np.ndarray[Any, np.dtype[np.bool_]]:
    """`operator_mask` over the test window, aligned to the person's samples."""
    timeline = Timeline.build(at(0), at(steps / 10), HZ)
    one, two = (resample(t, timeline, calibration) for t in (person, vehicle))
    return operator_mask(one, two, HZ, timeline.n)


def test_a_slow_close_up_forklift_moves_under_the_old_image_speed_gate() -> None:
    """The live geometry: ground speed 0.25 m/s is 25 px/s, 3.5% of a 720 px box per second."""
    boxes = np.array([p.box_xyxy for p in slow_forklift().points])
    speed = norm(image_velocity(boxes, HZ))[5:-5]
    assert (speed < STILL_HEIGHTS_PER_S * CLOSE_UP_PX[1]).all()


def test_the_seated_driver_of_a_slow_close_up_forklift_does_not_fire() -> None:
    assert run(RULE, [slow_forklift(), slow_driver()]) == []
    assert run(DISTANCE_ONLY, [slow_forklift(), slow_driver()]) == []
    assert len(run(RULE, [slow_forklift(), slow_driver()], operator_exclusion=False)) == 1


def test_the_ground_test_needs_a_calibration() -> None:
    """Without one only image speed says the vehicle moves: the slow driver is not linked."""
    vehicle, person = slow_forklift(with_foot_m=False), slow_driver(with_foot_m=False)
    assert mask(person, vehicle).all()
    assert not mask(person, vehicle, calibration=None).any()


@pytest.mark.parametrize("size_px", [CLOSE_UP_PX, FORKLIFT_PX], ids=["close-up", "normal"])
def test_a_slow_forklift_driving_up_to_a_standing_pedestrian_fires(
    size_px: tuple[float, float],
) -> None:
    """Safe side: outside the box first, then inside it, never moving with it."""
    steps = 120
    vehicle = walk_track(
        "cam1:forklift:1",
        [(2.0 + SLOW * i / 10, 5.0) for i in range(steps)],
        concept="forklift",
        size_px=size_px,
    )
    front = 2.0 + size_px[0] / 200 + 0.8  # 0.5 m beyond the front edge, 1.6 s of approach
    pedestrian = walk_track("cam1:person:3", [(front, 5.5)] * steps, size_px=PERSON_PX)
    assert not mask(pedestrian, vehicle, steps=steps).any()
    events = evaluate(
        RULE, [vehicle, pedestrian], calibration=CAL, zones={}, t0=at(0), t1=at(steps / 10)
    )
    assert [e.predicate_trace["subject"] for e in events] == [pedestrian.track_id]
    assert events[0].ts_end == at(steps / 10)  # still a pedestrian once inside the box


def test_a_pedestrian_who_walks_into_a_slow_forklifts_box_fires() -> None:
    """They cross in front of it (perpendicular), stop inside its box and stay."""
    walk = [(4.0, 6.5 - 0.1 * i) for i in range(10)] + [(4.0, 5.5)] * (STEPS - 10)
    pedestrian = walk_track("cam1:person:3", walk, size_px=PERSON_PX)
    assert not mask(pedestrian, slow_forklift()).any()
    assert len(run(DISTANCE_ONLY, [slow_forklift(), pedestrian])) == 1


def test_a_driver_stays_linked_while_the_forklift_stops_for_a_moment() -> None:
    """Drive 2 s, stand 2 s, drive 2 s: the still samples are not judged, the link holds."""
    xs = [2.0 + SPEED * min(i, 20) / 10 + SPEED * max(i - 40, 0) / 10 for i in range(STEPS)]
    vehicle = walk_track(
        "cam1:forklift:1", [(x, 5.0) for x in xs], concept="forklift", size_px=FORKLIFT_PX
    )
    person = walk_track("cam1:person:1", [(x, 4.5) for x in xs], size_px=PERSON_PX)
    assert mask(person, vehicle).all()
    assert run(DISTANCE_ONLY, [vehicle, person]) == []


def test_a_vehicle_track_born_around_its_driver_at_the_window_end_is_pending() -> None:
    """The tracker re-identified the forklift 0.3 s before the window ends (live 00:00:10)."""
    late = slow_forklift(4, start_s=5.7)
    seated = walk_track(
        "cam1:person:1",
        [(2.0, 4.5)] * 56 + [(2.0 + SLOW * i / 10, 4.5) for i in range(4)],
        size_px=PERSON_PX,
    )
    seated = Track(
        track_id=seated.track_id,
        camera_id="cam1",
        concept="person",
        points=seated.points[:56] + slow_driver(4, start_s=5.7).points,
    )
    assert run(DISTANCE_ONLY, [late, seated]) == []


def test_a_pending_boarding_needs_co_motion() -> None:
    """A fast forklift track born around a standing person at the window end still fires."""
    late = walk_track(
        "cam1:forklift:1",
        [(5.0 + SPEED * i / 10, 5.0) for i in range(4)],
        concept="forklift",
        size_px=FORKLIFT_PX,
        start_s=5.7,
    )
    standing = walk_track("cam1:person:3", [(5.0, 4.5)] * STEPS, size_px=PERSON_PX)
    assert len(run(DISTANCE_ONLY, [late, standing])) == 1


def test_a_box_that_only_changes_shape_is_no_evidence_of_riding() -> None:
    """Conservative side: the detector grows a parked forklift's box; its centre moves, not it."""
    grow = [(200.0 - 5.0 * i, -220.0, 600.0, 500.0) for i in range(STEPS)]
    vehicle = box_track("cam1:forklift:1", grow, concept="forklift")
    seated = walk_track("cam1:person:1", [(4.0, 4.5)] * STEPS, size_px=PERSON_PX)
    assert not mask(seated, vehicle).any()
    assert len(run(DISTANCE_ONLY, [vehicle, seated])) == 1


def test_slopes_and_reshape_rate_are_per_column_least_squares() -> None:
    values = np.array([[2.0 * i, 5.0, -1.0 * i] for i in range(12)])
    values[4] = np.nan
    assert slopes(values, 10.0, 1.0)[6] == pytest.approx((20.0, 0.0, -10.0))
    boxes = np.array([[10.0 - i, 0.0, 20.0 + i, 8.0 + 3 * i] for i in range(12)])
    assert reshape_rate(boxes, 10.0)[6] == pytest.approx((20.0, 30.0))


# --- the live driver-only clip (P1-07) ----------------------------------------------------------

LIVE = Path(__file__).resolve().parents[1] / "fixtures" / "rules" / "live_driver_only.json"


def _live() -> tuple[DecisionContext, list[Track], dict[str, Any]]:
    doc: dict[str, Any] = json.loads(LIVE.read_text("utf-8"))
    tracks = [
        Track(
            track_id=raw["track_id"],
            camera_id=raw["camera_id"],
            concept=raw["concept"],
            points=tuple(
                TrackPoint(
                    ts=datetime.fromisoformat(ts),
                    frame_idx=frame,
                    box_xyxy=(x1, y1, x2, y2),
                    foot_px=(fx, fy),
                )
                for ts, frame, x1, y1, x2, y2, fx, fy in raw["points"]
            ),
        )
        for raw in doc["tracks"]
    ]
    first = doc["recorded_windows"][0]
    context = DecisionContext.model_validate({**doc["context"], "t0": first[0], "t1": first[1]})
    return context, tracks, doc


def _windows(doc: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    recorded = [
        (datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in doc["recorded_windows"]
    ]
    span = timedelta(seconds=doc["window_s"])
    sliding = [(end - span, end) for end in map(datetime.fromisoformat, doc["chunk_ends"])]
    return recorded + sliding


def _live_events(
    context: DecisionContext, tracks: list[Track], doc: dict[str, Any]
) -> list[CandidateEvent]:
    found: dict[tuple[object, datetime], CandidateEvent] = {}
    for t0, t1 in _windows(doc):
        window = context.model_copy(update={"t0": t0, "t1": t1})
        for event in window.evaluate(tracks):
            found.setdefault((event.predicate_trace["subject"], event.ts_start), event)
    return list(found.values())


def test_the_live_driver_only_clip_has_no_candidates() -> None:
    """Every window the runtime evaluated on phone-23, 23:59:28-00:00:19 UTC: no candidate."""
    context, tracks, doc = _live()
    assert len(doc["chunk_ends"]) == 8
    assert _live_events(context, tracks, doc) == []


def test_the_live_fixture_reproduces_the_three_glimmer_candidates_without_exclusion() -> None:
    """Each recorded window, exclusion off, yields the candidate the runtime sent to Glimmer."""
    context, tracks, doc = _live()
    off = context.model_copy(update={"operator_exclusion": False})
    windows = _windows(doc)[: len(doc["recorded_windows"])]
    for (t0, t1), candidate in zip(windows, doc["recorded_candidates"], strict=True):
        events = off.model_copy(update={"t0": t0, "t1": t1}).evaluate(tracks)
        (event,) = [
            e for e in events if e.ts_start == datetime.fromisoformat(candidate["ts_start"])
        ]
        trace = event.predicate_trace
        assert trace["subject"] == candidate["subject"]
        assert trace["all.0.distance_m.value"] == pytest.approx(candidate["distance_m"], abs=1e-5)
        assert trace["all.1.moving.value"] == pytest.approx(candidate["moving_mps"], abs=1e-5)
