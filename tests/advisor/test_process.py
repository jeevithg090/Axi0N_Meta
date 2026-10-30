from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from rulecam.advisor.process import (
    PROCESS_TABLES,
    ProcessConfig,
    ProcessLog,
    QualityThresholds,
    Station,
    activity,
    assess_quality,
    build_process_log,
    fragment_rate,
    littles_law_check,
    stations_from_zones,
    write_duckdb,
)
from rulecam.contracts import Provenance, Track, Zone
from rulecam.perception.coverage import CoverageWatchdog
from rulecam.perception.intervals import Interval
from tests.advisor.dock_scenario import (
    WINDOW_S,
    ZONES,
    along,
    object_tracks,
    person_tracks,
    stay,
)
from tests.fakes import EPOCH

STATIONS = stations_from_zones(ZONES)
T1 = EPOCH + timedelta(seconds=WINDOW_S)


def at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def secs(ts: datetime) -> float:
    return (ts - EPOCH).total_seconds()


def build(
    tracks: list[Track] | None = None,
    provenance: Provenance = Provenance.RECORDED,
    config: ProcessConfig | None = None,
) -> ProcessLog:
    return build_process_log(
        object_tracks() if tracks is None else tracks,
        STATIONS,
        t0=EPOCH,
        t1=T1,
        provenance=provenance,
        config=config,
    )


class FixedCoverage:
    def __init__(self, fractions: dict[str, float]) -> None:
        self.fractions = fractions
        self.calls: list[tuple[str, datetime, datetime]] = []

    def watched_fraction(self, camera_id: str, t0: datetime, t1: datetime) -> float:
        self.calls.append((camera_id, t0, t1))
        return self.fractions[camera_id]


# --- stations ---------------------------------------------------------------------------------


def test_stations_from_zones_by_id_prefix() -> None:
    assert [(s.station_id, s.kind, s.camera_id) for s in STATIONS] == [
        ("aisle_1", "aisle", "cam1"),
        ("dock_1", "dock", "cam1"),
        ("dock_3", "dock", "cam3"),
    ]
    extra = Zone(zone_id="docking_area", camera_id="cam1", kind="polygon", polygon_px=((0, 0),))
    assert stations_from_zones([extra]) == ()


def test_station_zone_needs_a_polygon() -> None:
    buffer = Zone(zone_id="dock_9", camera_id="cam1", kind="buffer", buffer_of_concept="truck")
    with pytest.raises(ValueError, match="needs an image polygon"):
        stations_from_zones([buffer])


# --- intervals and cases ----------------------------------------------------------------------


def test_two_docks_give_correct_case_durations() -> None:
    log = build()
    assert [
        (c.case_id, secs(c.start), secs(c.end), c.wait_s, c.handling_s, c.post_dwell_s, c.complete)
        for c in log.cases
    ] == [
        ("case-dock_1-001", 20, 140, 40, 50, 30, True),
        ("case-dock_3-001", 30, 90, 30, 25, 5, True),
        ("case-dock_3-002", 200, 300, 50, 20, 30, False),
    ]
    for case in log.cases:
        assert case.wait_s + case.handling_s + case.post_dwell_s == pytest.approx(case.turnaround_s)


def test_station_intervals_per_phase() -> None:
    log = build()
    dock_1 = {
        kind: [(secs(i.start), secs(i.end)) for i in log.intervals if i.kind == kind]
        for kind in ("truck_present", "waiting", "handling", "post_dwell")
    }
    assert {k: [s for s in v if s[0] < 150] for k, v in dock_1.items()} == {
        "truck_present": [(20, 140), (30, 90)],
        "waiting": [(20, 50), (30, 60), (80, 90)],
        "handling": [(50, 80), (60, 85), (90, 110)],
        "post_dwell": [(85, 90), (110, 140)],
    }
    blocked = [(secs(i.start), secs(i.end)) for i in log.intervals if i.kind == "aisle_blocked"]
    assert blocked == [(150, 180), (200, 220)]
    phase = [i for i in log.intervals if i.kind in {"waiting", "handling", "post_dwell"}]
    assert all(i.case_id is not None and i.station is not None for i in phase)


def test_events_follow_the_visits_and_respect_window_edges() -> None:
    log = build()
    assert [(secs(e.ts), e.station, e.activity) for e in log.events] == [
        (20, "dock_1", "truck_arrived"),
        (30, "dock_3", "truck_arrived"),
        (50, "dock_1", "handling_started"),
        (60, "dock_1", "pallet_staged"),
        (60, "dock_3", "handling_started"),
        (85, "dock_3", "handling_ended"),
        (90, "dock_3", "truck_departed"),
        (110, "dock_1", "handling_ended"),
        (140, "dock_1", "truck_departed"),
        (150, "aisle_1", "aisle_blocked"),
        (180, "aisle_1", "aisle_cleared"),
        (200, "aisle_1", "aisle_blocked"),
        (200, "dock_3", "truck_arrived"),
        (220, "aisle_1", "aisle_cleared"),
        (250, "dock_3", "handling_started"),
    ]
    assert [e.event_id for e in log.events[:3]] == ["ev-00001", "ev-00002", "ev-00003"]


def test_event_objects_link_trucks_forklifts_and_pallets() -> None:
    log = build()
    links: dict[str, list[tuple[str, str]]] = {}
    for rel in log.event_objects:
        links.setdefault(rel.event_id, []).append((rel.object_id, rel.qualifier))
    by_event = {(secs(e.ts), e.activity): links[e.event_id] for e in log.events}
    assert by_event[(50, "handling_started")] == [
        ("truck-dock_1-001", "truck"),
        ("forklift-001", "handler"),
    ]
    assert by_event[(60, "pallet_staged")] == [
        ("pallet-001", "pallet"),
        ("truck-dock_1-001", "truck"),
    ]
    assert by_event[(150, "aisle_blocked")] == [("pallet-002", "blocker")]
    assert by_event[(200, "aisle_blocked")] == [("forklift-001", "blocker")]
    assert [(o.object_id, o.type) for o in log.objects] == [
        ("forklift-001", "forklift"),
        ("forklift-002", "forklift"),
        ("pallet-001", "pallet"),
        ("pallet-002", "pallet"),
        ("truck-dock_1-001", "truck"),
        ("truck-dock_3-001", "truck"),
        ("truck-dock_3-002", "truck"),
    ]


def test_visit_in_progress_at_the_start_has_no_arrival() -> None:
    tracks = [
        along("truck", "cam1", stay(0, 60, 2.0, 1.5)),
        along("forklift", "cam1", stay(0, 30, 2.0, 2.0)),
    ]
    log = build(tracks)
    assert [e.activity for e in log.events] == ["handling_ended", "truck_departed"]
    assert not log.cases[0].complete


def test_unhandled_visit_is_all_waiting() -> None:
    log = build([along("truck", "cam3", stay(100, 160, 8.0, 1.5))])
    case = log.cases[0]
    assert (case.wait_s, case.handling_s, case.post_dwell_s) == (60, 0, 0)
    assert [e.activity for e in log.events] == ["truck_arrived", "truck_departed"]


def test_forklift_active_versus_idle() -> None:
    moving = along("forklift", "cam1", [(0, 0.0, 4.5), (10, 5.0, 4.5), (20, 5.0, 4.5)])
    active, idle = activity(moving.points, ProcessConfig())
    assert [(secs(i.start), secs(i.end)) for i in active] == [(0, 10)]
    assert [(secs(i.start), secs(i.end)) for i in idle] == [(10, 20)]
    log = build([moving])
    kinds = {i.kind: (secs(i.start), secs(i.end), i.object_id, i.station) for i in log.intervals}
    assert kinds == {
        "forklift_active": (0, 10, "forklift-001", None),
        "forklift_idle": (10, 20, "forklift-001", None),
    }


def test_slow_forklift_below_threshold_is_idle() -> None:
    crawling = along("forklift", "cam1", [(0, 0.0, 4.5), (10, 1.5, 4.5)])  # 0.15 m/s
    active, idle = activity(crawling.points, ProcessConfig())
    assert active == []
    assert [(secs(i.start), secs(i.end)) for i in idle] == [(0, 10)]


def test_uncalibrated_forklift_has_no_activity() -> None:
    blind = along("forklift", "cam1", [(0, 0.0, 4.5), (10, 5.0, 4.5)], calibrated=False)
    assert activity(blind.points, ProcessConfig()) == ([], [])


def test_broken_tracks_are_stitched_into_one_object() -> None:
    first = along("forklift", "cam1", stay(0, 10, 2.0, 4.5))
    second = along("forklift", "cam1", stay(11.5, 20, 2.1, 4.5))  # 1.5 s later, 10 px away
    far = along("forklift", "cam1", stay(11.5, 20, 8.0, 4.5))
    other_camera = along("forklift", "cam3", stay(11.5, 20, 2.0, 4.5))
    overlapping = along("forklift", "cam1", stay(5, 15, 2.0, 4.5))
    log = build([first, second, far, other_camera, overlapping])
    assert [o.object_id for o in log.objects] == [f"forklift-00{n}" for n in range(1, 5)]
    idle = [(secs(i.start), secs(i.end)) for i in log.intervals if i.object_id == "forklift-001"]
    assert idle == [(0, 10), (11.5, 20)]  # one object; the 1.5 s join is longer than max_hold_s


def test_output_ignores_track_order_and_is_deterministic() -> None:
    tracks = object_tracks()
    shuffled = tracks[:]
    random.Random(7).shuffle(shuffled)  # noqa: S311 - test data order, not security
    assert build(shuffled) == build(tracks)
    assert build(tracks).model_dump_json() == build(tracks).model_dump_json()


def test_points_outside_the_window_are_ignored() -> None:
    log = build_process_log(
        object_tracks(), STATIONS, t0=at(100), t1=at(160), provenance=Provenance.RECORDED
    )
    assert [(c.station, secs(c.start), secs(c.end), c.complete) for c in log.cases] == [
        ("dock_1", 100, 140, False)
    ]


def test_input_validation() -> None:
    with pytest.raises(ValueError, match="must be after"):
        build_process_log([], STATIONS, t0=T1, t1=EPOCH, provenance=Provenance.RECORDED)
    twice = (*STATIONS, STATIONS[0])
    with pytest.raises(ValueError, match="unique"):
        build_process_log([], twice, t0=EPOCH, t1=T1, provenance=Provenance.RECORDED)
    with pytest.raises(ValueError, match="at least 3"):
        Station(station_id="dock_x", kind="dock", camera_id="cam1", polygon_px=((0, 0), (1, 1)))


# --- provenance and DuckDB --------------------------------------------------------------------


def test_provenance_propagates_to_every_row() -> None:
    log = build(provenance=Provenance.SIMULATED)
    provenances = {
        *(o.provenance for o in log.objects),
        *(e.provenance for e in log.events),
        *(r.provenance for r in log.event_objects),
        *(i.provenance for i in log.intervals),
        *(c.provenance for c in log.cases),
    }
    assert all((log.objects, log.events, log.event_objects, log.intervals, log.cases))
    assert provenances == {Provenance.SIMULATED}
    con = duckdb.connect()
    write_duckdb(log, con)
    for table in PROCESS_TABLES:
        assert con.execute(f"SELECT DISTINCT provenance FROM {table}").fetchall() == [  # noqa: S608
            ("simulated",)
        ]
    report = assess_quality(
        log, object_tracks(), STATIONS, coverage=FixedCoverage({"cam1": 1.0, "cam3": 1.0})
    )
    assert report.provenance is Provenance.SIMULATED


def test_duckdb_tables_answer_station_queries() -> None:
    con = duckdb.connect()
    write_duckdb(build(), con)
    assert con.execute(
        "SELECT station, count(*), sum(turnaround_s), sum(wait_s) FROM cases GROUP BY station "
        "ORDER BY station"
    ).fetchall() == [("dock_1", 1, 120.0, 40.0), ("dock_3", 2, 160.0, 80.0)]
    dangling = con.execute(
        "SELECT count(*) FROM event_object eo LEFT JOIN objects o USING (object_id) "
        "WHERE o.object_id IS NULL"
    ).fetchone()
    assert dangling == (0,)
    first = con.execute("SELECT min(ts) FROM events").fetchone()
    assert first == (datetime(2026, 10, 30, 12, 0, 20),)  # naive UTC
    handling = con.execute(
        "SELECT sum(seconds) FROM station_intervals WHERE kind = 'handling' AND station = 'dock_1'"
    ).fetchone()
    assert handling == (50.0,)


def test_write_duckdb_replaces_tables_and_handles_an_empty_log() -> None:
    con = duckdb.connect()
    write_duckdb(build(), con)
    write_duckdb(build([]), con)
    for table in PROCESS_TABLES:
        assert con.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)  # noqa: S608


def test_naive_timestamps_are_taken_as_utc() -> None:
    naive = EPOCH.replace(tzinfo=None)
    log = build_process_log(
        [], [], t0=naive, t1=naive + timedelta(seconds=10), provenance=Provenance.RECORDED
    )
    con = duckdb.connect()
    write_duckdb(log, con)
    assert log.t0.tzinfo is None
    assert EPOCH.astimezone(UTC).replace(tzinfo=None) == naive


# --- quality gates ----------------------------------------------------------------------------


def test_littles_law_passes_on_consistent_data() -> None:
    log = build()
    check = littles_law_check(log, object_tracks(), STATIONS)
    assert check.occupancy == pytest.approx(280 / 300)
    assert check.lambda_w == pytest.approx(280 / 300)
    assert check.arrival_rate_per_h == pytest.approx(3 / 300 * 3600)
    assert check.mean_turnaround_s == pytest.approx(280 / 3)
    assert check.rel_error == pytest.approx(0.0)
    assert check.status == "pass"


def test_littles_law_fails_on_corrupted_data() -> None:
    tracks = object_tracks()
    tracks.append(along("truck", "cam1", stay(20, 140, 2.2, 1.5)))  # duplicated truck track
    log = build(tracks)
    assert len(log.cases) == 3  # the case builder still sees one visit
    check = littles_law_check(log, tracks, STATIONS)
    assert check.occupancy == pytest.approx(400 / 300)
    assert check.rel_error == pytest.approx(120 / 400)
    assert check.status == "fail"


def test_littles_law_not_applicable_without_trucks() -> None:
    check = littles_law_check(build([]), [], STATIONS)
    assert (check.status, check.rel_error, check.mean_turnaround_s) == ("n/a", None, None)


def test_fragment_rate_counts_short_tracks() -> None:
    window = Interval(start=EPOCH, end=T1)
    tracks = [*object_tracks(), along("forklift", "cam1", stay(10, 11, 6.0, 4.5))]
    rate, n = fragment_rate([*tracks, *person_tracks()], ["cam1", "cam3"], window)
    assert rate == pytest.approx(1 / 8)
    assert n == 8
    assert fragment_rate(tracks, ["cam9"], window) == (None, 0)


def test_quality_report_gates() -> None:
    coverage = FixedCoverage({"cam1": 0.95, "cam3": 0.85})
    log = build()
    report = assess_quality(log, object_tracks(), STATIONS, coverage=coverage)
    assert {(c, secs(t0), secs(t1)) for c, t0, t1 in coverage.calls} == {
        ("cam1", 0, 300),
        ("cam3", 0, 300),
    }
    assert report.watched_by_camera == {"cam1": 0.95, "cam3": 0.85}
    assert {g.name: (g.value, g.status) for g in report.gates} == {
        "watched_fraction": (0.85, "pass"),
        "littles_law": (pytest.approx(0.0), "pass"),
        "fragment_rate": (0.0, "pass"),
        "incomplete_cases": (pytest.approx(1 / 3), "fail"),
    }
    assert (report.n_cases, report.n_incomplete, report.n_tracks) == (3, 1, 7)
    assert not report.passed
    relaxed = assess_quality(
        log,
        object_tracks(),
        STATIONS,
        coverage=coverage,
        thresholds=QualityThresholds(max_incomplete_share=0.5),
    )
    assert relaxed.passed
    assert relaxed.gate("incomplete_cases").status == "pass"


def test_unwatched_cameras_fail_the_watched_gate() -> None:
    report = assess_quality(build(), object_tracks(), STATIONS, coverage=CoverageWatchdog())
    assert report.gate("watched_fraction").value == 0.0
    assert report.gate("watched_fraction").status == "fail"


def test_empty_window_gates_are_not_applicable() -> None:
    log = build_process_log([], [], t0=EPOCH, t1=T1, provenance=Provenance.RECORDED)
    report = assess_quality(log, [], [], coverage=FixedCoverage({}))
    assert {g.name: g.status for g in report.gates} == dict.fromkeys(
        ("watched_fraction", "littles_law", "fragment_rate", "incomplete_cases"), "n/a"
    )
    assert report.passed
