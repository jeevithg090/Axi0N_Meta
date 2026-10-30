"""Temporal operators: persist, absence, rate and sequence (P1-06b required tests)."""

from __future__ import annotations

import numpy as np
import pytest

from rulecam.contracts import CandidateEvent, RuleIR, Track
from rulecam.rules import evaluate
from rulecam.rules.temporal import rising_edges, run_lengths
from tests.rules.helpers import (
    CAL,
    INSIDE_M,
    OUTSIDE_M,
    SINK_ZONE,
    Segment,
    at,
    make_rule,
    segments,
    walk_track,
)

ZONES = {"sink_zone": SINK_ZONE, "door": SINK_ZONE.model_copy(update={"zone_id": "door"})}
INSIDE = {"op": "inside", "object": "person", "zone": "sink_zone"}


def run(rule: RuleIR, tracks: list[Track], seconds: float = 120.0) -> list[CandidateEvent]:
    return evaluate(rule, tracks, calibration=CAL, zones=ZONES, t0=at(0), t1=at(seconds))


def person(*parts: Segment, track_id: str = "cam1:person:1", start_s: float = 0.0) -> Track:
    return walk_track(track_id, segments(*parts), start_s=start_s)


# --- persist ----------------------------------------------------------------------------------

PERSIST = make_rule({"op": "persist", "of": INSIDE, "seconds": 1.0})


@pytest.mark.parametrize(("inside_s", "fires"), [(0.9, False), (1.0, True), (1.1, True)])
def test_persist_one_second(inside_s: float, fires: bool) -> None:
    track = person((OUTSIDE_M, 1), (INSIDE_M, inside_s), (OUTSIDE_M, 3))
    events = run(PERSIST, [track])
    assert bool(events) is fires
    if fires:
        assert events[0].ts_start == at(1.9)
        assert events[0].measurement is not None
        assert (events[0].measurement.value, events[0].measurement.unit) == (1.0, "s")


def test_persist_restarts_after_an_interruption() -> None:
    track = person(
        (OUTSIDE_M, 1), (INSIDE_M, 0.6), (OUTSIDE_M, 0.1), (INSIDE_M, 0.6), (OUTSIDE_M, 3)
    )
    assert run(PERSIST, [track]) == []


# --- absence ----------------------------------------------------------------------------------

ABSENCE = make_rule(
    {"op": "absence", "object": "staff", "zone": "sink_zone", "seconds": 20},
    concepts={"staff": "staff uniform"},
)


def staff(*parts: Segment) -> Track:
    return walk_track("cam1:staff:1", segments(*parts), concept="staff uniform")


def test_absence_fires_after_the_zone_is_empty_long_enough() -> None:
    events = run(ABSENCE, [staff((INSIDE_M, 5), (OUTSIDE_M, 40))], seconds=40)
    assert len(events) == 1
    event = events[0]
    assert event.predicate_trace["subject"] == "scene"
    assert event.ts_start == at(5 + 20 - 0.1)
    assert event.measurement is not None
    assert (event.measurement.value, event.measurement.unit) == (20.0, "s")


def test_absence_resets_when_someone_returns() -> None:
    track = staff((INSIDE_M, 5), (OUTSIDE_M, 15), (INSIDE_M, 5), (OUTSIDE_M, 15))
    assert run(ABSENCE, [track], seconds=40) == []


def test_absence_on_an_empty_camera_fires_from_the_zone_camera() -> None:
    events = run(ABSENCE, [], seconds=25)
    assert [e.camera_id for e in events] == ["cam1"]


# --- rate -------------------------------------------------------------------------------------

RATE = make_rule(
    {
        "op": "rate",
        "of": {"op": "inside", "object": "forklift", "zone": "sink_zone"},
        "per_minute_gt": 2,
    },
    concepts={"forklift": "forklift"},
)


def passes(n: int, every_s: float) -> Track:
    parts: list[Segment] = [(OUTSIDE_M, 1.0)]
    for _ in range(n):
        parts += [(INSIDE_M, 2.0), (OUTSIDE_M, every_s - 2.0)]
    return walk_track("cam1:forklift:1", segments(*parts), concept="forklift")


@pytest.mark.parametrize(("n", "every_s", "fires"), [(3, 15, True), (2, 15, False), (3, 40, False)])
def test_rate_of_rising_edges_per_minute(n: int, every_s: float, fires: bool) -> None:
    events = run(RATE, [passes(n, every_s)], seconds=180)
    assert bool(events) is fires
    if fires:
        assert events[0].ts_start == at(1 + 2 * 15)  # the third pass
        assert events[0].measurement is not None
        assert (events[0].measurement.value, events[0].measurement.unit) == (3.0, "1/min")


def test_rate_counts_entries_of_different_objects_together() -> None:
    tracks = [
        walk_track(
            f"cam1:forklift:{i}",
            segments((OUTSIDE_M, 1), (INSIDE_M, 2), (OUTSIDE_M, 2)),
            concept="forklift",
            start_s=i * 10,
        )
        for i in range(3)
    ]
    assert len(run(RATE, tracks, seconds=60)) == 1


# --- sequence ---------------------------------------------------------------------------------


def sequence_rule(semantics: str) -> RuleIR:
    return make_rule(
        {
            "op": "sequence",
            "semantics": semantics,
            "trigger": {"op": "inside", "object": "person", "zone": "sink_zone"},
            "require_within_s": 10,
            "requirement": {"op": "inside", "object": "person", "zone": "badge"},
        },
        zones={
            "badge": {
                "kind": "polygon",
                "camera_id": "cam1",
                "polygon_px": [[400, 400], [500, 400], [500, 500], [400, 500]],
            }
        },
    )


BADGE_M = (4.5, 4.5)


@pytest.mark.parametrize(
    ("parts", "violation"),
    [
        ([(OUTSIDE_M, 2), (BADGE_M, 1), (OUTSIDE_M, 5), (INSIDE_M, 2)], False),
        ([(OUTSIDE_M, 2), (BADGE_M, 1), (OUTSIDE_M, 12), (INSIDE_M, 2)], True),
        ([(OUTSIDE_M, 2), (INSIDE_M, 2), (BADGE_M, 1), (OUTSIDE_M, 2)], True),
    ],
)
def test_sequence_past(parts: list[Segment], violation: bool) -> None:
    events = run(sequence_rule("past"), [person(*parts)], seconds=30)
    assert bool(events) is violation


@pytest.mark.parametrize(
    ("parts", "violation"),
    [
        ([(OUTSIDE_M, 2), (INSIDE_M, 2), (OUTSIDE_M, 3), (BADGE_M, 1), (OUTSIDE_M, 20)], False),
        ([(OUTSIDE_M, 2), (INSIDE_M, 2), (OUTSIDE_M, 25)], True),
    ],
)
def test_sequence_future_fires_at_the_deadline(parts: list[Segment], violation: bool) -> None:
    events = run(sequence_rule("future"), [person(*parts)], seconds=40)
    assert bool(events) is violation
    if violation:
        assert events[0].ts_start == at(12)  # entry at 2 s + 10 s


def test_sequence_future_is_undecided_past_the_window() -> None:
    track = person((OUTSIDE_M, 2), (INSIDE_M, 2), (OUTSIDE_M, 3))
    assert run(sequence_rule("future"), [track], seconds=7) == []


def test_sequence_with_a_requirement_about_another_concept() -> None:
    rule = make_rule(
        {
            "op": "sequence",
            "semantics": "past",
            "trigger": {"op": "inside", "object": "person", "zone": "sink_zone"},
            "require_within_s": 10,
            "requirement": {"op": "state", "object": "light", "state": "on"},
        }
    )
    entry = person((OUTSIDE_M, 2), (INSIDE_M, 2))
    light = walk_track("cam1:light:1", [OUTSIDE_M] * 10, concept="on light")
    assert run(rule, [entry], seconds=10)
    assert run(rule, [entry, light], seconds=10) == []


def test_a_subject_already_true_at_the_window_start_is_not_an_entry() -> None:
    track = person((INSIDE_M, 3))
    assert run(sequence_rule("past"), [track], seconds=5) == []


def test_run_lengths_and_edges() -> None:
    truth = np.array([1, 1, 0, 1, 1, 1, 0], dtype=bool)
    assert run_lengths(truth).tolist() == [1, 2, 0, 1, 2, 3, 0]
    assert rising_edges(truth, first_counts=True).tolist() == [1, 0, 0, 1, 0, 0, 0]
    assert rising_edges(truth, first_counts=False).tolist() == [0, 0, 0, 1, 0, 0, 0]
