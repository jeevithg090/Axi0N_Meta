from __future__ import annotations

import duckdb
import pytest

from rulecam.advisor.dock_stats import DockAdvisorStats
from rulecam.advisor.metrics import CATALOGUE
from rulecam.contracts import Provenance
from tests.advisor.dock_log import Visit, add_interval, add_visits, at, empty_log, shifts

T0, T1 = at(0), at(900)
CATALOGUE_IDS = {
    "turnaround", "wait_for_forklift", "handling", "post_dwell", "forklift_active_share",
    "concurrent_forklifts", "aisle_blocked_min", "near_miss_per_forklift_hour",
}  # fmt: skip


def stats_for(con: duckdb.DuckDBPyConnection, *, forklifts: int | None = None) -> DockAdvisorStats:
    return DockAdvisorStats(
        con, shifts=shifts(3, 300.0), coverage=lambda *_: 0.95, bin_minutes=5, forklifts=forklifts
    )


def case_log() -> duckdb.DuckDBPyConnection:
    con = empty_log()
    visits = [
        Visit(10, 60, 100), Visit(100, 120, 100), Visit(350, 180, 100),
        Visit(400, 240, 100), Visit(700, 300, 100), Visit(800, 360, 100),
    ]  # fmt: skip
    add_visits(con, "dock_3", visits, prefix="c")
    return con


def bin_log() -> duckdb.DuckDBPyConnection:
    con = empty_log()
    add_interval(con, "a", "forklift_active", "dock_3", (0, 300), object_id="F1")
    add_interval(con, "b", "forklift_active", "dock_3", (0, 150), object_id="F2")
    add_interval(con, "c", "forklift_active", "dock_3", (300, 450), object_id="F1")
    add_interval(con, "d", "forklift_idle", "dock_3", (450, 600), object_id="F1")
    add_interval(con, "e", "aisle_blocked", "aisle_1", (100, 250), object_id="P1")
    return con


def test_catalogue_is_the_eight_vetted_metrics() -> None:
    assert stats_for(empty_log()).catalogue() == CATALOGUE_IDS
    assert set(CATALOGUE) == CATALOGUE_IDS
    assert all(d.definition and d.unit for d in CATALOGUE.values())


def test_case_metrics_are_means_in_minutes() -> None:
    stats = stats_for(case_log())
    wait = stats.metric("wait_for_forklift", "dock_3", T0, T1)
    assert wait is not None
    assert wait.value == pytest.approx(3.5)  # mean of 1..6 minutes
    assert wait.lo <= wait.value <= wait.hi
    assert (wait.unit, wait.n, wait.n_tracks, wait.n_shifts) == ("min", 6, 6, 3)
    assert wait.coverage == 0.95
    assert wait.provenance is Provenance.RECORDED
    assert wait.group_by == ("station",)
    handling = stats.metric("handling", "dock_3", T0, T1)
    turnaround = stats.metric("turnaround", "dock_3", T0, T1)
    dwell = stats.metric("post_dwell", "dock_3", T0, T1)
    assert handling is not None
    assert turnaround is not None
    assert dwell is not None
    assert handling.value == pytest.approx(100 / 60)
    assert dwell.value == pytest.approx(20 / 60)
    assert turnaround.value == pytest.approx(wait.value + handling.value + dwell.value)


def test_enough_tracks_and_shifts_meets_the_minimum_k() -> None:
    wait = stats_for(case_log()).metric("wait_for_forklift", "dock_3", T0, T1)
    assert wait is not None
    assert wait.meets_minimum_k is True


def test_too_few_tracks_or_shifts_does_not_meet_the_minimum_k() -> None:
    few_tracks = stats_for(case_log()).metric("wait_for_forklift", "dock_3", T0, at(450))
    assert few_tracks is not None
    assert few_tracks.n_tracks == 4
    assert few_tracks.meets_minimum_k is False
    one_shift = DockAdvisorStats(case_log(), shifts=shifts(1, 900.0), coverage=lambda *_: 1.0)
    wait = one_shift.metric("wait_for_forklift", "dock_3", T0, T1)
    assert wait is not None
    assert wait.n_shifts == 1
    assert wait.meets_minimum_k is False


def test_the_sql_is_the_query_that_ran_and_names_the_id() -> None:
    stats = stats_for(case_log())
    first = stats.metric("wait_for_forklift", "dock_3", T0, T1)
    again = stats.metric("wait_for_forklift", "dock_3", T0, T1)
    other_window = stats.metric("wait_for_forklift", "dock_3", T0, at(800))
    assert first is not None
    assert again is not None
    assert other_window is not None
    assert first.id == again.id
    assert first.id != other_window.id
    assert first.id.startswith("mr_")
    assert "station = 'dock_3'" in first.sql
    assert "TIMESTAMP '" in first.sql
    assert stats.result(first.id) == first
    assert stats.result("mr_unknown") is None


def test_forklift_share_and_concurrency_are_means_over_time_bins() -> None:
    stats = stats_for(bin_log())
    share = stats.metric("forklift_active_share", "dock_3", T0, T1)
    concurrent = stats.metric("concurrent_forklifts", "dock_3", T0, T1)
    assert share is not None
    assert concurrent is not None
    assert share.value == pytest.approx(0.75)  # bin 1: all active, bin 2: half; bin 3 unseen
    assert concurrent.value == pytest.approx(1.0)  # 1.5 forklifts in bin 1, 0.5 in bin 2
    assert (share.n, share.n_tracks) == (2, 2)
    assert share.unit == "share"


def test_aisle_blocked_minutes_per_hour_counts_empty_bins_as_zero() -> None:
    blocked = stats_for(bin_log()).metric("aisle_blocked_min", "dock_3", T0, T1)
    assert blocked is not None
    assert blocked.value == pytest.approx(10.0)  # 30 min/h in one of three bins
    assert blocked.n == 3
    assert blocked.n_tracks == 1


def test_aisle_metric_is_none_without_an_aisle() -> None:
    assert stats_for(case_log()).metric("aisle_blocked_min", "dock_3", T0, T1) is None


def test_near_misses_per_forklift_hour_need_their_events_table() -> None:
    con = bin_log()
    assert stats_for(con).metric("near_miss_per_forklift_hour", "dock_3", T0, T1) is None
    con.execute("CREATE TABLE near_miss_events (ts TIMESTAMP, station VARCHAR, provenance VARCHAR)")
    for seconds in (100, 200):
        con.execute(
            "INSERT INTO near_miss_events VALUES (?, 'dock_3', 'recorded')",
            [at(seconds).replace(tzinfo=None)],
        )
    result = stats_for(con).metric("near_miss_per_forklift_hour", "dock_3", T0, T1)
    assert result is not None
    # bin 1: 2 events over 450 forklift-seconds = 16 per hour; bin 2: 0 events over 300 s
    assert result.value == pytest.approx(8.0)


def test_nothing_to_measure_gives_none() -> None:
    assert stats_for(empty_log()).metric("turnaround", "dock_3", T0, T1) is None
    assert stats_for(empty_log()).metric("forklift_active_share", "dock_3", T0, T1) is None


def test_mixed_provenance_is_refused() -> None:
    con = case_log()
    add_visits(con, "dock_3", [Visit(500, 10, 10)], prefix="s", provenance="simulated")
    with pytest.raises(ValueError, match="mix provenance"):
        stats_for(con).metric("turnaround", "dock_3", T0, T1)


def test_unknown_metric_and_bad_station_ids_are_refused() -> None:
    stats = stats_for(case_log())
    with pytest.raises(ValueError, match="not in the metric catalogue"):
        stats.metric("revenue", "dock_3", T0, T1)
    with pytest.raises(ValueError, match="bad station id"):
        stats.metric("turnaround", "dock_3' OR '1'='1", T0, T1)
