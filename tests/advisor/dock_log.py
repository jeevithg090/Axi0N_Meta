"""Synthetic dock process logs in DuckDB for the Site Advisor statistics tests (P4-08b)."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import duckdb

from rulecam.advisor.process import ProcessLog, write_duckdb
from rulecam.contracts import Provenance
from tests.fakes import EPOCH


def empty_log(provenance: Provenance = Provenance.RECORDED) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with the process-log tables and no rows."""
    con = duckdb.connect(":memory:")
    log = ProcessLog(
        t0=EPOCH,
        t1=EPOCH + timedelta(hours=1),
        provenance=provenance,
        objects=(),
        events=(),
        event_objects=(),
        intervals=(),
        cases=(),
    )
    write_duckdb(log, con)
    return con


def at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def naive(seconds: float) -> datetime:
    return at(seconds).replace(tzinfo=None)


def add_interval(
    con: duckdb.DuckDBPyConnection,
    name: str,
    kind: str,
    station: str,
    span_s: tuple[float, float],
    *,
    object_id: str | None = None,
    provenance: str = "recorded",
) -> None:
    start_s, end_s = span_s
    con.execute(
        "INSERT INTO station_intervals VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
        [name, kind, station, naive(start_s), naive(end_s), end_s - start_s, object_id,
         provenance],
    )  # fmt: skip


def add_blocker(
    con: duckdb.DuckDBPyConnection,
    name: str,
    station: str,
    span_s: tuple[float, float],
    object_id: str,
    *,
    provenance: str = "recorded",
) -> None:
    """An aisle blocked by an object, recorded the way the real builder does it: the interval
    row carries no object; the blocker is an event-object link."""
    add_interval(con, name, "aisle_blocked", station, span_s, provenance=provenance)
    con.execute(
        "INSERT INTO events VALUES (?, 'aisle_blocked', ?, ?, ?)",
        [f"ev-{name}", naive(span_s[0]), station, provenance],
    )
    con.execute(
        "INSERT INTO event_object VALUES (?, ?, 'blocker', ?)",
        [f"ev-{name}", object_id, provenance],
    )


@dataclass(frozen=True)
class Visit:
    arrive_s: float
    wait_s: float
    handling_s: float
    post_dwell_s: float = 20.0


def simulate_queue(
    seed: int, n: int, mean_gap_s: float, mean_service_s: float, start_s: float = 0.0
) -> list[Visit]:
    """A FIFO single-server queue with exponential gaps and service: M/M/1."""
    rng = random.Random(seed)  # noqa: S311 - seeded simulation, not cryptography
    clock, free_at = start_s, start_s
    visits = []
    for _ in range(n):
        clock += rng.expovariate(1.0 / mean_gap_s)
        service = rng.expovariate(1.0 / mean_service_s)
        begin = max(clock, free_at)
        free_at = begin + service
        visits.append(Visit(clock, begin - clock, service))
    return visits


def add_visits(
    con: duckdb.DuckDBPyConnection,
    station: str,
    visits: Sequence[Visit],
    *,
    prefix: str,
    provenance: str = "recorded",
) -> None:
    for i, v in enumerate(visits):
        turnaround = v.wait_s + v.handling_s + v.post_dwell_s
        con.execute(
            "INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?)",
            [f"{prefix}{i}", station, f"truck-{prefix}{i}", naive(v.arrive_s),
             naive(v.arrive_s + turnaround), v.wait_s, v.handling_s, v.post_dwell_s, turnaround,
             provenance],
        )  # fmt: skip
        begin = v.arrive_s + v.wait_s
        add_interval(
            con, f"{prefix}h{i}", "handling", station, (begin, begin + v.handling_s),
            provenance=provenance,
        )  # fmt: skip


def shifts(count: int, length_s: float) -> list[tuple[datetime, datetime]]:
    return [(at(i * length_s), at((i + 1) * length_s)) for i in range(count)]
