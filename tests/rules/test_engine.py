"""Engine-level behaviour: purity, determinism, episodes, cameras and traces (P1-06)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rulecam.contracts import CandidateEvent, RuleIR, Track
from rulecam.rules import RuleEvaluationError, evaluate, rule_ir_hash
from tests.rules.helpers import (
    CAL,
    INSIDE_M,
    OUTSIDE_M,
    SINK_ZONE,
    at,
    make_rule,
    segments,
    walk_track,
)

CONCEPTS = {"person": "person", "forklift": "forklift"}
NEAR_AND_MOVING = make_rule(
    {
        "op": "all",
        "of": [
            {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0},
            {"op": "moving", "object": "forklift", "speed_mps_gt": 0.2},
        ],
    },
    concepts=CONCEPTS,
    severity="high",
)


def as_json(events: list[CandidateEvent]) -> str:
    return json.dumps([e.model_dump(mode="json") for e in events], sort_keys=False)


def scenario() -> list[Track]:
    forklift = walk_track(
        "cam1:forklift:1", [(0.1 * i, 0.0) for i in range(80)], concept="forklift"
    )
    walker = walk_track("cam1:person:1", [(4.0, 1.0)] * 80)
    bystander = walk_track("cam1:person:2", [(9.0, 9.0)] * 80)
    return [forklift, walker, bystander]


def run(
    rule: RuleIR,
    tracks: list[Track],
    *,
    t0: datetime | None = None,
    t1: datetime | None = None,
    hz: float = 10.0,
    cooldown_s: float = 0.0,
    plan_version_hash: str = "",
) -> list[CandidateEvent]:
    return evaluate(
        rule,
        tracks,
        calibration=CAL,
        zones={"sink_zone": SINK_ZONE},
        t0=t0 or at(0),
        t1=t1 or at(10),
        hz=hz,
        cooldown_s=cooldown_s,
        plan_version_hash=plan_version_hash,
    )


def test_all_combines_subjects_and_reports_the_distance() -> None:
    events = run(NEAR_AND_MOVING, scenario())
    assert len(events) == 1
    event = events[0]
    assert event.predicate_trace["subject"] == "cam1:person:1"
    assert event.predicate_trace["all"] is True
    assert event.predicate_trace["all.0.distance_m"] is True
    assert event.predicate_trace["all.1.moving"] is True
    assert event.predicate_trace["all.1.moving.value"] == pytest.approx(1.0)
    assert event.measurement is not None
    assert event.measurement.unit == "m"
    assert event.measurement.value < 2.0
    assert event.severity == "high"
    assert event.strip_paths == ()


def test_identical_inputs_give_byte_identical_output() -> None:
    first = as_json(run(NEAR_AND_MOVING, scenario()))
    second = as_json(run(NEAR_AND_MOVING, scenario()))
    assert first == second
    assert first != "[]"


@st.composite
def random_tracks(draw: st.DrawFn) -> list[Track]:
    count = draw(st.integers(min_value=1, max_value=6))
    tracks = []
    for index in range(count):
        concept = draw(st.sampled_from(["person", "forklift"]))
        start = draw(st.integers(min_value=0, max_value=30))
        steps = draw(
            st.lists(st.tuples(st.integers(-3, 3), st.integers(-3, 3)), min_size=1, max_size=40)
        )
        x, y = draw(st.integers(0, 40)) / 10, draw(st.integers(0, 40)) / 10
        positions = []
        for dx, dy in steps:
            x, y = x + dx / 10, y + dy / 10
            positions.append((x, y))
        tracks.append(
            walk_track(f"cam1:{concept}:{index}", positions, concept=concept, start_s=start / 10)
        )
    return tracks


@settings(max_examples=60, deadline=None)
@given(data=st.data(), tracks=random_tracks())
def test_shuffling_the_track_list_gives_the_same_output(
    data: st.DataObject, tracks: list[Track]
) -> None:
    shuffled = data.draw(st.permutations(tracks))
    assert as_json(run(NEAR_AND_MOVING, shuffled)) == as_json(run(NEAR_AND_MOVING, tracks))


def test_empty_tracks_give_no_candidates() -> None:
    assert run(NEAR_AND_MOVING, []) == []
    dwell = make_rule(
        {"op": "dwell", "object": "hands", "zone": "sink_zone", "total_s_gte": 20},
    )
    assert run(dwell, []) == []


def test_irrelevant_concepts_do_not_change_the_result() -> None:
    noise = walk_track("cam1:cat:1", [(4.0, 1.0)] * 80, concept="cat")
    assert as_json(run(NEAR_AND_MOVING, [*scenario(), noise])) == as_json(
        run(NEAR_AND_MOVING, scenario())
    )


def test_points_outside_the_window_are_ignored() -> None:
    tracks = scenario()
    early = [walk_track("cam1:person:1", [(0.0, 0.5)] * 50, start_s=-6.0)]
    assert as_json(run(NEAR_AND_MOVING, [*tracks, *early])) == as_json(run(NEAR_AND_MOVING, tracks))


INSIDE = make_rule({"op": "inside", "object": "person", "zone": "sink_zone"})


@pytest.mark.parametrize(("gap_s", "episodes"), [(1.5, 1), (1.9, 1), (2.0, 2), (2.5, 2)])
def test_an_episode_ends_after_two_seconds_false(gap_s: float, episodes: int) -> None:
    path = segments((INSIDE_M, 1), (OUTSIDE_M, gap_s), (INSIDE_M, 1), (OUTSIDE_M, 3))
    events = run(INSIDE, [walk_track("cam1:person:1", path)])
    assert len(events) == episodes


def test_cooldown_suppresses_repeat_episodes() -> None:
    path = segments((INSIDE_M, 1), (OUTSIDE_M, 3), (INSIDE_M, 1), (OUTSIDE_M, 3))
    track = walk_track("cam1:person:1", path)
    assert len(run(INSIDE, [track])) == 2
    assert len(run(INSIDE, [track], cooldown_s=5.0)) == 1


def test_each_subject_gets_its_own_episode() -> None:
    one = walk_track("cam1:person:1", [INSIDE_M] * 20)
    two = walk_track("cam1:person:2", [INSIDE_M] * 20)
    events = run(INSIDE, [two, one])
    assert sorted(e.predicate_trace["subject"] for e in events) == [
        "cam1:person:1",
        "cam1:person:2",
    ]
    assert len({e.event_id for e in events}) == 2


def test_cameras_are_evaluated_separately_with_their_own_calibration() -> None:
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0}, concepts=CONCEPTS
    )
    cam1 = [
        walk_track("cam1:person:1", [(0.0, 1.0)] * 20, with_foot_m=False),
        walk_track("cam1:forklift:1", [(0.0, 0.0)] * 20, concept="forklift", with_foot_m=False),
    ]
    cam2 = [
        walk_track("cam2:person:1", [(0.0, 1.0)] * 20, camera_id="cam2", with_foot_m=False),
        walk_track(
            "cam2:forklift:1",
            [(0.0, 0.0)] * 20,
            camera_id="cam2",
            concept="forklift",
            with_foot_m=False,
        ),
    ]
    events = run(rule, cam2 + cam1)
    assert [e.camera_id for e in events] == ["cam1"]  # cam2 has no calibration


def test_not_holds_only_while_the_subject_is_seen() -> None:
    rule = make_rule({"op": "not", "of": {"op": "inside", "object": "person", "zone": "sink_zone"}})
    events = run(rule, [walk_track("cam1:person:1", [OUTSIDE_M] * 20)])
    assert len(events) == 1
    assert events[0].ts_end == at(2.4)  # 2 s of points + 0.5 s hold
    assert events[0].predicate_trace["not"] is True
    assert events[0].predicate_trace["not.inside"] is False


def test_any_of_scene_level_children() -> None:
    rule = make_rule(
        {
            "op": "any",
            "of": [
                {"op": "state", "object": "door", "state": "open"},
                {"op": "count", "object": "box", "gt": 5},
            ],
        }
    )
    door = walk_track("cam1:door:1", [INSIDE_M] * 20, concept="open door")
    events = run(rule, [door])
    assert len(events) == 1
    assert events[0].predicate_trace["any.0.state"] is True
    assert events[0].predicate_trace["any.1.count"] is False


def test_invalid_windows_raise() -> None:
    with pytest.raises(RuleEvaluationError, match="before"):
        run(INSIDE, [], t0=at(5), t1=at(0))
    with pytest.raises(RuleEvaluationError, match="hz"):
        run(INSIDE, [], hz=0)


def test_hashes_identify_rule_and_window() -> None:
    events = run(INSIDE, [walk_track("cam1:person:1", [INSIDE_M] * 20)], plan_version_hash="p1")
    event = events[0]
    assert event.rule_ir_hash == rule_ir_hash(INSIDE)
    assert event.plan_version_hash == "p1"
    other_rule = INSIDE.model_copy(update={"version": 2})
    assert rule_ir_hash(other_rule) != rule_ir_hash(INSIDE)
    moved = run(INSIDE, [walk_track("cam1:person:1", [(0.51, 0.5)] * 20)])
    assert moved[0].track_window_hash != event.track_window_hash
    assert moved[0].event_id == event.event_id  # same rule, subject and start


def test_candidates_round_trip_through_json() -> None:
    for event in run(NEAR_AND_MOVING, scenario()):
        assert CandidateEvent.model_validate_json(event.model_dump_json()) == event


def test_timeline_sample_times() -> None:
    events = run(INSIDE, [walk_track("cam1:person:1", [INSIDE_M] * 3, start_s=0.25)])
    assert events[0].ts_start == at(0.3)
    assert events[0].ts_end - events[0].ts_start == timedelta(seconds=0.6)
