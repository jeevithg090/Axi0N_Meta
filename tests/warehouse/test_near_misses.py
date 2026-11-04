"""Near-miss log and heatmap on the recorded T02 and T04 chains."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from rulecam.contracts import EventRecord, Track
from rulecam.warehouse.near_misses import (
    distance_limit,
    foot_at,
    heatmap,
    measures_distance,
    near_misses,
    trace_number,
)
from tests.fakes import make_track
from tests.warehouse.conftest import DAY, T02_EVENTS, T02_PLAN, T04_PLAN


def test_t02_near_misses_carry_the_measured_numbers_and_the_verdict(
    t02_records: list[EventRecord],
) -> None:
    rows = near_misses(t02_records)
    assert {r.event_id for r in rows} == set(T02_EVENTS)
    newest = rows[0]
    assert newest.event_id == "ev-49940b44a5ce03bffe26313f"
    assert newest.limit_m == 4
    assert newest.separation_m == pytest.approx(3.991023)
    assert newest.closest_m == pytest.approx(3.814843)
    assert newest.ttc_s is None  # "none" in the trace: not on a collision course
    assert newest.vehicle_speed_mps == pytest.approx(0.328294)
    assert newest.calibration == "person_height_1.7m"
    assert newest.verdict == "violation"
    assert newest.verdict_score == pytest.approx(0.99)
    assert newest.alert is True
    assert newest.route == "auto"
    assert newest.actions == ("dry-run: would notify supervisor",)
    assert newest.plan_id == T02_PLAN
    assert newest.rule_text.startswith("Keep people at least 4 metres")


def test_t04_driver_candidates_are_logged_as_rejected(t04_records: list[EventRecord]) -> None:
    rows = near_misses(t04_records, plan_id=T04_PLAN)
    assert len(rows) == 3
    assert {r.verdict for r in rows} == {"no_violation"}
    assert {r.alert for r in rows} == {False}


def test_filters_by_plan_rule_and_camera(t02_records: list[EventRecord]) -> None:
    assert near_misses(t02_records, plan_id="plan-other") == []
    assert near_misses(t02_records, rule_ids=["another-rule"]) == []
    assert near_misses(t02_records, camera_id="cam-x") == []
    assert len(near_misses(t02_records, plan_id=T02_PLAN, camera_id="phone-97")) == 2


def test_disputes_acks_and_false_alarm_labels_are_joined(t02_records: list[EventRecord]) -> None:
    event = T02_EVENTS[1]
    last = t02_records[-1]
    extra = [
        _record(last, 1, "ack", {"event_id": event, "role": "supervisor"}),
        _record(
            last,
            2,
            "dispute",
            {"event_id": event, "dispute_id": "dp-1", "dispute": {"outcome": "pending"}},
        ),
        _record(
            last,
            3,
            "dispute",
            {"event_id": event, "dispute_id": "dp-1", "dispute": {"outcome": "upheld"}},
        ),
        _record(last, 4, "label", {"event_id": event, "label": "false_alarm"}),
    ]
    row = next(r for r in near_misses([*t02_records, *extra]) if r.event_id == event)
    assert row.acked_by == ("supervisor",)
    assert row.disputes == ("upheld",)
    assert row.regression is True


def _record(last: EventRecord, n: int, kind: str, payload: dict[str, object]) -> EventRecord:
    return EventRecord.model_validate(
        {
            "seq": last.seq + n,
            "kind": kind,
            "ts": last.ts + timedelta(seconds=n),
            "site": last.site,
            "payload": payload,
            "prev_hash": "0" * 64,
            "hash": "0" * 64,
        }
    )


def test_places_each_near_miss_from_the_track_store(t02_records: list[EventRecord]) -> None:
    calls: list[tuple[str, datetime, datetime]] = []

    def window(camera: str, t0: datetime, t1: datetime) -> list[Track]:
        calls.append((camera, t0, t1))
        return [make_track("dc3eaa8b", camera_id=camera, feet_m=((3.0, 5.0),), start=t0)]

    rows = near_misses(t02_records, tracks=window)
    newest = rows[0]
    assert newest.foot_px == (300.0, 500.0)
    assert calls
    assert all(c[0] == "phone-97" for c in calls)


def test_foot_at_matches_the_subject_and_picks_the_nearest_point() -> None:
    start = datetime(2026, 11, 2, 23, 43, 45, tzinfo=datetime.now().astimezone().tzinfo)
    track = make_track("abc", feet_m=((1.0, 1.0), (2.0, 2.0), (3.0, 3.0)), start=start)
    assert foot_at([track], "phone-1:person:abc", start + timedelta(seconds=0.21)) == (300.0, 300.0)
    assert foot_at([track], "phone-1:person:zzz", start) is None


def test_trace_helpers_read_nested_and_root_nodes() -> None:
    trace = {"all.0.distance_m.separation_m": 1.5, "distance_m.value": 2, "moving.value": "x"}
    assert trace_number(trace, ".distance_m", "separation_m") == 1.5
    assert trace_number({"distance_m.value": 2}, ".distance_m", "value") == 2.0
    assert trace_number(trace, ".moving", "value") is None
    rule = {"predicate": {"op": "all", "of": [{"op": "distance_m", "a": "p", "b": "f", "lt": 2}]}}
    assert distance_limit(rule) == 2.0
    assert measures_distance(rule)
    assert not measures_distance({"predicate": {"op": "moving", "object": "f"}})


def test_heatmap_bins_points_on_the_frame_grid(t02_records: list[EventRecord]) -> None:
    rows = [
        r.model_copy(update={"foot_px": (639.0, 700.0)}) if i == 0 else r
        for i, r in enumerate(near_misses(t02_records))
    ]
    heat = heatmap(rows, camera_id="phone-97", day=DAY, width=1280, height=720, cols=4, rows=3)
    assert heat.unplaced == 1
    assert len(heat.points) == 1
    assert heat.cells[2][1] == 1
    assert sum(sum(r) for r in heat.cells) == 1
    assert heatmap(rows, camera_id="other", day=DAY).points == ()
