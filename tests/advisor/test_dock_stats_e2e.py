"""Site Advisor statistics on a log from the real process-log builder, not hand-made rows."""

from __future__ import annotations

from datetime import timedelta

import duckdb
import pytest

from rulecam.advisor.dock_stats import DockAdvisorStats
from rulecam.advisor.process import build_process_log, stations_from_zones, write_duckdb
from rulecam.contracts import Provenance
from tests.advisor.dock_scenario import WINDOW_S, ZONES, object_tracks
from tests.fakes import EPOCH

T1 = EPOCH + timedelta(seconds=WINDOW_S)


@pytest.fixture
def stats() -> DockAdvisorStats:
    con = duckdb.connect(":memory:")
    log = build_process_log(
        object_tracks(),
        stations_from_zones(ZONES),
        t0=EPOCH,
        t1=T1,
        provenance=Provenance.RECORDED,
    )
    write_duckdb(log, con)
    return DockAdvisorStats(con, shifts=[(EPOCH, T1)], coverage=lambda *_: 0.9, forklifts=2)


def test_case_metrics_read_the_visits_the_builder_wrote(stats: DockAdvisorStats) -> None:
    wait = stats.metric("wait_for_forklift", "dock_3", EPOCH, T1)
    assert wait is not None
    # dock_3 had two visits; the second is cut by the window edge and is not complete
    assert wait.n == 1
    assert wait.value == pytest.approx(30 / 60)
    handling = stats.metric("handling", "dock_1", EPOCH, T1)
    assert handling is not None
    assert handling.value == pytest.approx(50 / 60)


def test_aisle_blockers_are_counted_from_the_event_links(stats: DockAdvisorStats) -> None:
    blocked = stats.metric("aisle_blocked_min", "dock_1", EPOCH, T1)
    assert blocked is not None
    assert blocked.value == pytest.approx(50 / 300 * 60)  # 50 s blocked in one 300 s bin
    assert blocked.n_tracks == 2  # a pallet and a parked forklift
    assert blocked.provenance is Provenance.RECORDED


@pytest.mark.xfail(
    strict=True,
    reason="the builder writes forklift rows with no station (process.py `_add_activity`); "
    "waiting on P1 to attribute them to the dock zone they are in",
)
def test_forklift_metrics_need_forklift_rows_to_carry_a_station(stats: DockAdvisorStats) -> None:
    share = stats.metric("forklift_active_share", "dock_1", EPOCH, T1)
    assert share is not None
