"""Dwell boundary cases (P1-06 required tests; MP §7.3 handwash fix)."""

from __future__ import annotations

import numpy as np
import pytest

from rulecam.rules import evaluate
from rulecam.rules.temporal import merge_runs, runs
from tests.rules.helpers import (
    CAL,
    INSIDE_M,
    OUTSIDE_M,
    SINK_ZONE,
    Segment,
    at,
    box_track,
    make_rule,
    segments,
    walk_track,
)

DWELL = {"op": "dwell", "object": "hands", "zone": "sink_zone", "total_s_gte": 20, "max_gap_s": 3}
RULE = make_rule(DWELL, concepts={"hands": "hands"})


def qualifying_washes(positions: list[tuple[float, float] | None]) -> list[float]:
    """Run the dwell rule on a hands track; return the measured totals of qualifying washes."""
    hands = walk_track("cam1:hands:1", positions, concept="hands")
    events = evaluate(
        RULE, [hands], calibration=CAL, zones={"sink_zone": SINK_ZONE}, t0=at(0), t1=at(60)
    )
    return [e.measurement.value for e in events if e.measurement is not None]


@pytest.mark.parametrize(
    ("name", "parts", "qualifies"),
    [
        ("19.5 s wash -> violation", [(INSIDE_M, 19.5), (OUTSIDE_M, 10)], False),
        ("20.5 s wash -> pass", [(INSIDE_M, 20.5), (OUTSIDE_M, 10)], True),
        ("exactly 20 s -> pass", [(INSIDE_M, 20.0), (OUTSIDE_M, 10)], True),
        (
            "12 s + 2 s gap + 12 s -> pass",
            [(INSIDE_M, 12), (OUTSIDE_M, 2), (INSIDE_M, 12), (OUTSIDE_M, 5)],
            True,
        ),
        (
            "12 s + 4 s gap + 12 s -> violation",
            [(INSIDE_M, 12), (OUTSIDE_M, 4), (INSIDE_M, 12), (OUTSIDE_M, 5)],
            False,
        ),
        (
            "gap of exactly max_gap_s is bridged",
            [(INSIDE_M, 12), (OUTSIDE_M, 3), (INSIDE_M, 12), (OUTSIDE_M, 5)],
            True,
        ),
        (
            "two short washes never add up across a long gap",
            [(INSIDE_M, 10), (OUTSIDE_M, 10), (INSIDE_M, 10), (OUTSIDE_M, 5)],
            False,
        ),
    ],
)
def test_dwell_boundaries(name: str, parts: list[Segment], qualifies: bool) -> None:
    washes = qualifying_washes(segments(*parts))
    assert bool(washes) is qualifies, name


def test_gaps_are_excluded_from_the_total() -> None:
    # 12 s + 2 s gap + 9 s: 23 s elapsed but only 21 s of washing; fires at 20 s of washing.
    parts: list[Segment] = [(INSIDE_M, 12), (OUTSIDE_M, 2), (INSIDE_M, 9), (OUTSIDE_M, 5)]
    assert qualifying_washes(segments(*parts)) == [20.0]
    parts = [(INSIDE_M, 12), (OUTSIDE_M, 2), (INSIDE_M, 7.5), (OUTSIDE_M, 5)]
    assert qualifying_washes(segments(*parts)) == []


def test_detection_dropouts_inside_the_zone_are_bridged() -> None:
    # The hands track disappears for 2 s (no detections) between two 12 s washes.
    parts: list[Segment] = [(INSIDE_M, 12), (None, 2), (INSIDE_M, 12), (OUTSIDE_M, 5)]
    assert qualifying_washes(segments(*parts))


def test_wash_fires_at_threshold_with_measured_total() -> None:
    hands = walk_track("cam1:hands:1", segments((INSIDE_M, 25), (OUTSIDE_M, 5)), concept="hands")
    events = evaluate(
        RULE, [hands], calibration=CAL, zones={"sink_zone": SINK_ZONE}, t0=at(0), t1=at(60)
    )
    assert len(events) == 1
    event = events[0]
    assert event.ts_start == at(19.9)  # the 200th sample: 20 s of washing
    assert event.ts_end == at(24.9)
    assert event.measurement is not None
    assert (event.measurement.value, event.measurement.unit) == (20.0, "s")
    assert event.predicate_trace["dwell"] is True
    assert event.predicate_trace["dwell.value"] == 20.0


def test_hands_of_a_person_are_associated_by_box() -> None:
    rule = make_rule(
        {**DWELL, "object": "person.hands"}, concepts={"person": "person", "hands": "hands"}
    )
    person_box = (0.0, -100.0, 100.0, 160.0)  # contains the sink zone
    person = box_track("cam1:person:1", [person_box] * 300, concept="person")
    inside_hand = (40.0, 40.0, 60.0, 60.0)
    hands = box_track("cam1:hands:1", [inside_hand] * 250, concept="hands")
    stranger = box_track("cam1:person:2", [(500.0, 0.0, 600.0, 200.0)] * 300, concept="person")
    events = evaluate(
        rule,
        [person, hands, stranger],
        calibration=CAL,
        zones={"sink_zone": SINK_ZONE},
        t0=at(0),
        t1=at(30),
    )
    assert [e.predicate_trace["subject"] for e in events] == ["cam1:person:1"]


def test_hands_far_from_a_person_are_not_theirs() -> None:
    rule = make_rule(
        {**DWELL, "object": "person.hands"}, concepts={"person": "person", "hands": "hands"}
    )
    person = box_track("cam1:person:1", [(500.0, 0.0, 600.0, 200.0)] * 300, concept="person")
    hands = box_track("cam1:hands:1", [(40.0, 40.0, 60.0, 60.0)] * 250, concept="hands")
    events = evaluate(
        rule, [person, hands], calibration=CAL, zones={"sink_zone": SINK_ZONE}, t0=at(0), t1=at(30)
    )
    assert events == []


def test_run_helpers() -> None:
    truth = np.array([0, 1, 1, 0, 0, 1, 0, 1], dtype=bool)
    assert runs(truth) == [(1, 2), (5, 5), (7, 7)]
    assert merge_runs(runs(truth), 1) == [(1, 2), (5, 7)]
    assert merge_runs(runs(truth), 2) == [(1, 7)]
    assert runs(np.zeros(3, dtype=bool)) == []
