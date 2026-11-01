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


def test_forklift_metrics_read_station_less_activity_spans(stats: DockAdvisorStats) -> None:
    """The builder writes forklift activity with no station, so the query attributes it.

    `_forklift_spans` clips each station-less `forklift_active`/`forklift_idle` span to the
    dock's own `handling` intervals, for a forklift the dock's visit events link as a handler.
    Before this, the three forklift metrics returned nothing at all on a builder log.

    Both forklifts in this scenario cross their dock at about 0.07 m/s, under the 0.2 m/s
    `active_speed_mps` threshold, so the builder writes one long `forklift_idle` span for each
    and the true active share is 0. `tests/advisor/test_metrics.py` covers active spans.
    """
    share = stats.metric("forklift_active_share", "dock_1", EPOCH, T1)
    assert share is not None  # the value exists at all: that is the fix
    assert share.value == pytest.approx(0.0)
    assert share.n_tracks == 1  # forklift 1 only, not both site forklifts
    assert share.provenance is Provenance.RECORDED


def test_a_dock_is_not_credited_with_another_dock_s_forklift(stats: DockAdvisorStats) -> None:
    """Forklift 2 works dock_3 only, so its idle time must not land on dock_1 as well."""
    for station in ("dock_1", "dock_3"):
        share = stats.metric("forklift_active_share", station, EPOCH, T1)
        assert share is not None
        assert share.n_tracks == 1
