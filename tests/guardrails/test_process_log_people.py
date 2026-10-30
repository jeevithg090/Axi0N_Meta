"""Guardrail (rule R13, SA §5): people are never OCEL objects in the Site Advisor process log.

The process log is advisor-facing, so no person track, and no tracker id of any kind, may appear in
`objects`, `events`, `event_object`, `station_intervals` or `cases`.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from rulecam.advisor.process import (
    PERSON_CONCEPTS,
    PROCESS_TABLES,
    ProcessConfig,
    ProcessLog,
    build_process_log,
    stations_from_zones,
    write_duckdb,
)
from rulecam.contracts import Provenance, Track
from tests.advisor.dock_scenario import WINDOW_S, ZONES, object_tracks, person_tracks
from tests.fakes import EPOCH

PROCESS_SOURCE = Path(__file__).resolve().parents[2] / "src" / "rulecam" / "advisor" / "process.py"
STATIONS = stations_from_zones(ZONES)


def build(tracks: list[Track]) -> ProcessLog:
    return build_process_log(
        tracks,
        STATIONS,
        t0=EPOCH,
        t1=EPOCH + timedelta(seconds=WINDOW_S),
        provenance=Provenance.RECORDED,
    )


def table_cells(tracks: list[Track]) -> list[str]:
    con = duckdb.connect()
    write_duckdb(build(tracks), con)
    cells: list[str] = []
    for table in PROCESS_TABLES:
        for row in con.execute(f"SELECT * FROM {table}").fetchall():  # noqa: S608
            cells.extend(str(value) for value in row)
    return cells


def test_no_person_is_ever_an_ocel_object() -> None:
    con = duckdb.connect()
    write_duckdb(build([*object_tracks(), *person_tracks()]), con)
    types = {row[0] for row in con.execute("SELECT DISTINCT type FROM objects").fetchall()}
    assert types == {"truck", "pallet", "forklift"}
    linked = con.execute(
        "SELECT DISTINCT o.type FROM event_object eo JOIN objects o USING (object_id)"
    ).fetchall()
    assert {row[0] for row in linked} <= {"truck", "pallet", "forklift"}
    orphans = con.execute(
        "SELECT count(*) FROM event_object WHERE object_id NOT IN (SELECT object_id FROM objects)"
    ).fetchone()
    assert orphans == (0,)


def test_people_change_nothing_in_the_log() -> None:
    assert build([*object_tracks(), *person_tracks()]) == build(object_tracks())


def test_people_alone_produce_an_empty_log() -> None:
    assert table_cells(person_tracks()) == []


def test_no_tracker_id_appears_in_any_table() -> None:
    tracks = [*object_tracks(), *person_tracks()]
    cells = table_cells(tracks)
    assert cells
    for track in tracks:
        assert not any(track.track_id in cell for cell in cells), track.track_id
    assert not any("person" in cell or "worker" in cell for cell in cells)


@pytest.mark.parametrize("concept", [*sorted(PERSON_CONCEPTS), "Person", " worker "])
def test_config_refuses_to_map_people(concept: str) -> None:
    with pytest.raises(ValueError, match="people are never process objects"):
        ProcessConfig(object_types={concept: "truck"})


def test_process_log_source_never_touches_tracker_ids() -> None:
    assert "track_id" not in PROCESS_SOURCE.read_text(encoding="utf-8")
