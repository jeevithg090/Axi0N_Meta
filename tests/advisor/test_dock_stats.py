from __future__ import annotations

import duckdb
import pytest

from rulecam.advisor.dock_stats import DockAdvisorStats
from rulecam.advisor.pipeline import AdvisorStats
from rulecam.contracts import Change
from tests.advisor.dock_log import (
    Visit,
    add_interval,
    add_visits,
    at,
    empty_log,
    shifts,
    simulate_queue,
)

WINDOW_START = 100_000.0
T0, T1 = at(WINDOW_START), at(WINDOW_START + 12_000.0)


def dock_log() -> duckdb.DuckDBPyConnection:
    """dock_3 and dock_2 turn busy in the window; dock_1 keeps its usual quiet pattern."""
    con = empty_log()
    add_visits(con, "dock_3", simulate_queue(5, 150, 400.0, 80.0), prefix="d3b")
    add_visits(con, "dock_3", simulate_queue(7, 80, 130.0, 80.0, WINDOW_START), prefix="d3w")
    add_visits(con, "dock_1", simulate_queue(7, 150, 400.0, 80.0), prefix="d1b")
    add_visits(con, "dock_1", simulate_queue(8, 30, 400.0, 80.0, WINDOW_START), prefix="d1w")
    add_visits(con, "dock_2", simulate_queue(9, 150, 400.0, 80.0), prefix="d2b")
    add_visits(con, "dock_2", simulate_queue(11, 80, 130.0, 80.0, WINDOW_START), prefix="d2w")
    return con


def advisor(
    con: duckdb.DuckDBPyConnection | None = None, min_effect_min: float = 1.0
) -> DockAdvisorStats:
    window_shifts = [
        (at(WINDOW_START + i * 4000.0), at(WINDOW_START + (i + 1) * 4000.0)) for i in range(3)
    ]
    return DockAdvisorStats(
        con or dock_log(),
        shifts=[*window_shifts, *shifts(1, WINDOW_START)],
        coverage=lambda *_: 0.9,
        forklifts=1,
        min_effect_min=min_effect_min,
    )


def test_satisfies_the_pipeline_protocol() -> None:
    stats: AdvisorStats = advisor()
    assert "wait_for_forklift" in stats.catalogue()


def test_only_the_station_that_got_slower_is_flagged() -> None:
    results = advisor().detect_bottlenecks(["dock_3", "dock_1"], T0, T1)
    assert [r.station_id for r in results] == ["dock_3"]
    top = results[0]
    assert top.metric == "wait_for_forklift"
    assert top.meets_minimum_k
    assert top.n_tracks == top.n


def test_both_slow_stations_are_flagged_in_a_stable_order() -> None:
    first = advisor().detect_bottlenecks(["dock_1", "dock_2", "dock_3"], T0, T1)
    second = advisor().detect_bottlenecks(["dock_3", "dock_2", "dock_1"], T0, T1)
    assert {r.station_id for r in first} == {"dock_2", "dock_3"}
    assert [r.station_id for r in first] == [r.station_id for r in second]


def quiet_then_slightly_slower() -> duckdb.DuckDBPyConnection:
    con = empty_log()
    add_visits(con, "dock_3", [Visit(i * 300.0, 0.0, 80.0) for i in range(80)], prefix="b")
    window = [Visit(WINDOW_START + i * 300.0, 30.0, 80.0) for i in range(12)]
    add_visits(con, "dock_3", window, prefix="w")
    return con


def test_a_significant_but_immaterial_slowdown_is_not_flagged_unless_the_floor_is_lowered() -> None:
    con = quiet_then_slightly_slower()
    assert advisor(con).detect_bottlenecks(["dock_3"], T0, T1) == []
    lowered = advisor(con, min_effect_min=0.25).detect_bottlenecks(["dock_3"], T0, T1)
    assert [r.station_id for r in lowered] == ["dock_3"]


def test_unknown_or_unrecorded_stations_are_skipped() -> None:
    assert advisor().detect_bottlenecks(["dock_9"], T0, T1) == []
    assert advisor().detect_bottlenecks([], T0, T1) == []


def test_detected_results_can_be_looked_up_by_id() -> None:
    stats = advisor()
    (result,) = stats.detect_bottlenecks(["dock_3"], T0, T1)
    assert stats.result(result.id) == result


def test_gap_shares_of_the_components_add_up_to_the_whole_gap() -> None:
    stats = advisor()
    shares = {
        m: stats.gap_share(m, "dock_3", T0, T1)
        for m in ("wait_for_forklift", "handling", "post_dwell")
    }
    assert all(s is not None for s in shares.values())
    assert sum(s for s in shares.values() if s is not None) == pytest.approx(1.0)
    wait = shares["wait_for_forklift"]
    assert wait is not None
    assert wait > 0.9  # the extra time is waiting, not handling


def test_metrics_that_are_not_components_have_no_gap_share() -> None:
    assert advisor().gap_share("forklift_active_share", "dock_3", T0, T1) is None


def test_no_gap_share_without_a_baseline_or_without_data() -> None:
    stats = advisor()
    assert stats.gap_share("handling", "dock_3", at(0), at(1_000_000)) is None
    assert stats.gap_share("handling", "dock_9", T0, T1) is None


def test_adding_a_forklift_is_modelled_with_its_assumptions() -> None:
    result = advisor().what_if(Change(op="add_capacity", params={}), "dock_3", T0, T1)
    assert result is not None
    low, high = result.gain.interval
    assert low < high < 0
    assert result.gain.metric == "wait_for_forklift"
    assert result.gain.model_rung in ("erlang_c", "allen_cunneen")
    assert result.gain.assumptions["stable"] == "pass"
    assert result.p20_weekly_value > 0


def test_extra_servers_can_be_named_on_any_change() -> None:
    change = Change(op="resequence", params={"servers_added": "1"})
    assert advisor().what_if(change, "dock_3", T0, T1) is not None


@pytest.mark.parametrize(
    "change",
    [
        Change(op="move_location", params={}),
        Change(op="resequence", params={"servers_added": "many"}),
        Change(op="add_capacity", params={"servers_added": "-1"}),
        Change(op="reschedule_arrivals", params={"arrival_scv_scale": "-2"}),
    ],
)
def test_changes_without_a_model_have_no_what_if(change: Change) -> None:
    assert advisor().what_if(change, "dock_3", T0, T1) is None


def test_no_what_if_for_a_station_with_no_cases() -> None:
    assert advisor().what_if(Change(op="add_capacity", params={}), "dock_9", T0, T1) is None


def test_forklift_count_is_read_from_the_log_when_not_configured() -> None:
    con = dock_log()
    add_interval(
        con, "f1", "forklift_active", "dock_3", (WINDOW_START, WINDOW_START + 10), object_id="F1"
    )
    add_interval(
        con, "f2", "forklift_idle", "dock_3", (WINDOW_START, WINDOW_START + 10), object_id="F2"
    )
    stats = DockAdvisorStats(con, shifts=shifts(1, 1e6), coverage=lambda *_: 1.0)
    assert stats._forklift_count(T0, T1) == 2
    assert (
        DockAdvisorStats(empty_log(), shifts=[], coverage=lambda *_: 1.0)._forklift_count(T0, T1)
        == 1
    )
