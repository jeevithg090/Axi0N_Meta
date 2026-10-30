"""Backtester over the TrackStore Protocol and decision re-derivation (P1-07)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import pytest

from rulecam.contracts import EventRecord, RuleIR, Track, TrackPoint
from rulecam.rules import RuleEvaluationError, evaluate
from rulecam.rules.backtest import (
    DecisionContext,
    backtest,
    candidate_payload,
    diff_events,
    read_candidate_payload,
    rederive,
)
from tests.fakes import InMemoryTrackStore
from tests.rules.helpers import CAL, at, make_rule, walk_track

CONCEPTS = {"person": "person", "forklift": "forklift"}


def proximity(lt: float = 2.0) -> RuleIR:
    return make_rule(
        {
            "op": "all",
            "of": [
                {"op": "distance_m", "a": "person", "b": "forklift", "lt": lt},
                {"op": "moving", "object": "forklift", "speed_mps_gt": 0.2},
            ],
        },
        concepts=CONCEPTS,
        rule_id="forklift-proximity",
        severity="high",
    )


def shuttle(seconds: float, start_s: float = 0.0) -> Track:
    """A forklift driving back and forth along x between 0 and 10 m at 1 m/s."""
    steps = round(seconds * 10)
    path = []
    for i in range(steps):
        phase = (i / 10) % 20
        path.append((phase if phase < 10 else 20 - phase, 0.0))
    return walk_track("cam1:forklift:1", path, concept="forklift", start_s=start_s)


def visitor(index: int, start_s: float, y: float = 1.5) -> Track:
    return walk_track(f"cam1:person:{index}", [(5.0, y)] * 600, start_s=start_s)


def scene() -> list[Track]:
    return [shuttle(300), visitor(1, 30), visitor(2, 200, y=4.0)]


def stored(tracks: list[Track]) -> InMemoryTrackStore:
    store = InMemoryTrackStore()
    store.append(tracks)
    return store


def test_backtest_equals_live_evaluation() -> None:
    tracks = scene()
    report = backtest(proximity(), stored(tracks), "cam1", at(0), at(300), calibration=CAL)
    live = evaluate(proximity(), tracks, calibration=CAL, zones={}, t0=at(0), t1=at(300))
    assert report.events == tuple(live)
    assert report.count == len(live) > 0
    assert report.per_hour == pytest.approx(report.count / (300 / 3600))
    assert report.backtestable == "yes"
    assert report.missing_concepts == ()
    assert (
        report.headline() == f"would have fired {report.count}\N{MULTIPLICATION SIGN} earlier today"
    )


def test_backtest_reads_only_the_requested_camera() -> None:
    other = walk_track("cam2:person:1", [(5.0, 1.5)] * 600, camera_id="cam2", start_s=30)
    report = backtest(proximity(), stored([*scene(), other]), "cam1", at(0), at(300))
    assert {e.camera_id for e in report.events} == {"cam1"}


@pytest.mark.parametrize(
    ("tracks", "verdict", "missing"),
    [
        ([visitor(1, 0)], "partial", ("forklift",)),
        (
            [walk_track("cam1:cat:1", [(0.0, 0.0)] * 10, concept="cat")],
            "no",
            ("forklift", "person"),
        ),
    ],
)
def test_missing_concepts_reduce_backtestability(
    tracks: list[Track], verdict: str, missing: tuple[str, ...]
) -> None:
    report = backtest(proximity(), stored(tracks), "cam1", at(0), at(60))
    assert (report.backtestable, report.missing_concepts) == (verdict, missing)


def test_state_and_buffer_concepts_count_as_needed() -> None:
    rule = make_rule(
        {
            "op": "all",
            "of": [
                {"op": "state", "object": "door", "state": "open"},
                {"op": "inside", "object": "box", "zone": "egress"},
            ],
        },
        concepts={"door": "fire exit door", "box": "box"},
        zones={
            "egress": {"kind": "buffer", "camera_id": "cam1", "buffer_of": "door", "buffer_m": 1}
        },
    )
    tracks = [walk_track("cam1:door:1", [(0.0, 0.0)] * 10, concept="door/open")]
    report = backtest(rule, stored(tracks), "cam1", at(0), at(10))
    assert report.backtestable == "partial"
    assert report.missing_concepts == ("box", "door")


def test_v1_vs_v2_diff() -> None:
    tracks = [shuttle(300), visitor(1, 30, y=2.5)]  # 2.5 m from the forklift's path
    report = backtest(
        proximity(lt=3.0), stored(tracks), "cam1", at(0), at(300), baseline=proximity(lt=2.0)
    )
    assert report.diff is not None
    assert report.count > 0
    assert len(report.diff.added) == report.count
    assert report.diff.removed == ()
    reverse = diff_events(report.events, [], baseline_rule_ir_hash="x")
    assert reverse.removed == report.events


def test_backtest_needs_a_positive_window() -> None:
    with pytest.raises(RuleEvaluationError):
        backtest(proximity(), InMemoryTrackStore(), "cam1", at(10), at(10))


def test_headline_for_a_multi_day_window() -> None:
    report = backtest(proximity(), stored(scene()), "cam1", at(-86400), at(300))
    assert "between Oct 29" in report.headline()


@pytest.mark.slow
def test_six_hours_of_10hz_tracks_backtest_in_under_five_seconds() -> None:
    hours = 6
    tracks = [shuttle(hours * 3600)]
    tracks += [visitor(i, 600.0 * i + 30) for i in range(hours * 6)]  # one visitor every 10 min
    store = stored(tracks)
    started = time.perf_counter()
    report = backtest(proximity(), store, "cam1", at(0), at(hours * 3600), calibration=CAL)
    elapsed = time.perf_counter() - started
    assert report.count >= hours * 6
    assert elapsed < 5.0, f"backtest took {elapsed:.2f} s"


# --- rederive ---------------------------------------------------------------------------------


def record_for(tracks: list[Track]) -> tuple[EventRecord, DecisionContext]:
    context = DecisionContext(rule=proximity(), calibration=CAL, t0=at(0), t1=at(300))
    candidate = context.evaluate(tracks)[0]
    payload = json.loads(json.dumps(candidate_payload(candidate, context)))  # through JSONL
    record = EventRecord(
        seq=7,
        kind="candidate",
        ts=candidate.ts_start,
        site="dock",
        payload=payload,
        prev_hash="0" * 64,
        hash="f" * 64,
    )
    return record, context


def test_rederive_reproduces_an_untouched_record() -> None:
    tracks = scene()
    record, _ = record_for(tracks)
    assert rederive(record, stored(tracks)) is True


def test_rederive_rejects_a_tampered_payload() -> None:
    tracks = scene()
    record, _ = record_for(tracks)
    candidate = dict(record.payload["candidate"])  # type: ignore[call-overload]
    candidate["measurement"] = {**candidate["measurement"], "value": 3.5}
    tampered = record.model_copy(update={"payload": {**record.payload, "candidate": candidate}})
    assert rederive(tampered, stored(tracks)) is False


def test_rederive_rejects_a_swapped_rule() -> None:
    tracks = scene()
    record, context = record_for(tracks)
    looser = context.model_copy(update={"rule": proximity(lt=5.0)}).model_dump(mode="json")
    swapped = record.model_copy(update={"payload": {**record.payload, "context": looser}})
    assert rederive(swapped, stored(tracks)) is False


def test_rederive_rejects_changed_tracks() -> None:
    tracks = scene()
    record, _ = record_for(tracks)
    nudged = [
        t.model_copy(update={"points": tuple(_shift(p) for p in t.points)})
        if t.concept == "person"
        else t
        for t in tracks
    ]
    assert rederive(record, stored(nudged)) is False


def _shift(point: TrackPoint) -> TrackPoint:
    assert point.foot_m is not None
    return point.model_copy(update={"foot_m": (point.foot_m[0] + 0.01, point.foot_m[1])})


def test_rederive_rejects_malformed_payloads_and_other_kinds() -> None:
    tracks = scene()
    record, _ = record_for(tracks)
    broken = record.model_copy(update={"payload": {"candidate": {"nope": 1}}})
    assert rederive(broken, stored(tracks)) is False
    label = record.model_copy(update={"kind": "label"})
    with pytest.raises(RuleEvaluationError):
        rederive(label, stored(tracks))
    with pytest.raises(RuleEvaluationError):
        read_candidate_payload(label)


def test_rederive_rejects_a_context_that_no_longer_evaluates() -> None:
    tracks = scene()
    record, context = record_for(tracks)
    bad = context.model_dump(mode="json")
    bad["t1"] = (context.t0 - timedelta(seconds=1)).isoformat()
    broken = record.model_copy(update={"payload": {**record.payload, "context": bad}})
    assert rederive(broken, stored(tracks)) is False


def test_payload_round_trips() -> None:
    record, context = record_for(scene())
    candidate, parsed = read_candidate_payload(record)
    assert parsed == context
    assert candidate.rule_id == "forklift-proximity"
    assert isinstance(parsed.t0, datetime)
