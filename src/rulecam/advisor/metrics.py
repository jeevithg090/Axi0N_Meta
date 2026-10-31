# ruff: noqa: S608 - every interpolated value is a validated station id, an integer or a timestamp
"""Vetted metric catalogue for the dock station type (task P4-08b, SA §4 step 5).

Numbers come only from this catalogue; there is no free-form SQL. Each metric builds its query as
text with the station and window written in as literals, runs exactly that text, and keeps it on
the result. The result id is a hash of the text, so a recompute that runs the same query gets the
same id. Tracks are never a grouping key: queries group by time bin or by case.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import duckdb

from rulecam.advisor.pipeline import MIN_SHIFTS, MIN_TRACKS
from rulecam.advisor.stats.bootstrap import bootstrap_ci
from rulecam.contracts.core import Frozen, Provenance

STATION_ID: Final = re.compile(r"^[A-Za-z0-9_-]+$")
SECONDS_PER_MINUTE: Final = 60.0
Shift = tuple[datetime, datetime]
CoverageOf = Callable[[str, datetime, datetime], float]


class MetricResult(Frozen):
    """One measured number with its interval, evidence counts and the query behind it."""

    id: str
    metric: str
    station_id: str
    value: float
    lo: float
    hi: float
    unit: str
    n: int
    n_tracks: int
    n_shifts: int
    coverage: float
    provenance: Provenance
    group_by: tuple[str, ...]
    sql: str
    definition: str
    window_start: datetime
    window_end: datetime

    @property
    def meets_minimum_k(self) -> bool:
        """Whether it rests on enough tracks and shifts to be shown (SA §5)."""
        return self.n_tracks >= MIN_TRACKS and self.n_shifts >= MIN_SHIFTS


@dataclass(frozen=True)
class Scope:
    """What a metric is measured over."""

    station: str
    t0: datetime
    t1: datetime
    bin_s: int
    aisles: tuple[str, ...]


@dataclass(frozen=True)
class Query:
    """The two queries behind a metric.

    `units` returns `(value, ts, provenance)` rows, one per case or time bin. `objects` returns
    the ids of the physical objects (trucks, forklifts) behind them.
    """

    units: str
    objects: str


@dataclass(frozen=True)
class MetricDef:
    """A catalogue entry."""

    id: str
    definition: str
    unit: str
    build: Callable[[Scope], Query | None]


def _utc_naive(ts: datetime) -> datetime:
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo else ts


def timestamp_literal(ts: datetime) -> str:
    """Return a datetime as a UTC SQL `TIMESTAMP` literal (naive input is taken as UTC)."""
    return f"TIMESTAMP '{_utc_naive(ts).isoformat(sep=' ')}'"


def station_literal(station: str) -> str:
    """Return a station id as a SQL literal, rejecting anything that is not a plain id."""
    if not STATION_ID.fullmatch(station):
        raise ValueError(f"bad station id {station!r}")
    return f"'{station}'"


def _case_metric(expression: str) -> Callable[[Scope], Query | None]:
    def build(scope: Scope) -> Query:
        t0, t1 = timestamp_literal(scope.t0), timestamp_literal(scope.t1)
        where = (
            f"station = {station_literal(scope.station)} AND complete "
            f"AND arrived_ts >= {t0} AND arrived_ts < {t1}"
        )
        return Query(
            units=(
                f"SELECT {expression} AS value, arrived_ts AS ts, provenance FROM cases "
                f"WHERE {where} ORDER BY arrived_ts, case_id"
            ),
            objects=f"SELECT DISTINCT truck_object_id FROM cases WHERE {where}",
        )

    return build


def _bins(scope: Scope) -> str:
    step = f"INTERVAL {scope.bin_s} SECOND"
    t0, t1 = timestamp_literal(scope.t0), timestamp_literal(scope.t1)
    return (
        f"bins AS (SELECT t AS b0, LEAST(t + {step}, {t1}) AS b1 FROM "
        f"generate_series({t0}, {t1} - INTERVAL 1 SECOND, {step}) AS g(t))"
    )


OVERLAP: Final = "GREATEST(0, epoch(LEAST(i.end_ts, b.b1)) - epoch(GREATEST(i.start_ts, b.b0)))"


def _forklift_join(scope: Scope) -> str:
    return (
        f"FROM bins b JOIN station_intervals i ON i.station = {station_literal(scope.station)} "
        "AND i.kind IN ('forklift_active', 'forklift_idle') "
        "AND i.start_ts < b.b1 AND i.end_ts > b.b0"
    )


def _forklift_objects(scope: Scope) -> str:
    return (
        f"WITH {_bins(scope)} SELECT DISTINCT i.object_id {_forklift_join(scope)} "
        "WHERE i.object_id IS NOT NULL"
    )


def _forklift_bins(value: str) -> Callable[[Scope], Query | None]:
    """Per-bin value over the forklift intervals at a station; bins with none are skipped."""

    def build(scope: Scope) -> Query:
        return Query(
            units=(
                f"WITH {_bins(scope)} SELECT {value} AS value, b.b0 AS ts, "
                f"string_agg(DISTINCT i.provenance, ',') AS provenance {_forklift_join(scope)} "
                f"GROUP BY b.b0, b.b1 HAVING SUM({OVERLAP}) > 0 ORDER BY b.b0"
            ),
            objects=_forklift_objects(scope),
        )

    return build


def _aisle_blocked(scope: Scope) -> Query | None:
    if not scope.aisles:
        return None
    stations = ", ".join(station_literal(a) for a in scope.aisles)
    joined = (
        f"FROM bins b LEFT JOIN station_intervals i ON i.station IN ({stations}) "
        "AND i.kind = 'aisle_blocked' AND i.start_ts < b.b1 AND i.end_ts > b.b0"
    )
    # LEAST and GREATEST skip NULLs, so an empty bin from the LEFT JOIN needs an explicit zero
    blocked = f"COALESCE(SUM(CASE WHEN i.start_ts IS NULL THEN 0 ELSE {OVERLAP} END), 0)"
    return Query(
        units=(
            f"WITH {_bins(scope)} SELECT "
            f"{blocked} / (epoch(b.b1) - epoch(b.b0)) * 60 AS value, b.b0 AS ts, "
            f"string_agg(DISTINCT i.provenance, ',') AS provenance {joined} "
            "GROUP BY b.b0, b.b1 ORDER BY b.b0"
        ),
        objects=(
            f"WITH {_bins(scope)} SELECT DISTINCT i.object_id {joined} "
            "WHERE i.object_id IS NOT NULL"
        ),
    )


def _near_misses(scope: Scope) -> Query | None:
    station = station_literal(scope.station)
    hours = f"SUM({OVERLAP}) / 3600.0"
    return Query(
        units=(
            f"WITH {_bins(scope)} SELECT "
            f"(SELECT COUNT(*) FROM near_miss_events n WHERE n.station = {station} "
            f"AND n.ts >= b.b0 AND n.ts < b.b1) / ({hours}) AS value, b.b0 AS ts, "
            f"string_agg(DISTINCT i.provenance, ',') AS provenance {_forklift_join(scope)} "
            f"GROUP BY b.b0, b.b1 HAVING SUM({OVERLAP}) > 0 ORDER BY b.b0"
        ),
        objects=_forklift_objects(scope),
    )


CATALOGUE: Final[Mapping[str, MetricDef]] = {
    d.id: d
    for d in (
        MetricDef(
            "turnaround",
            "Minutes from a truck arriving at the dock to leaving it, for complete visits that "
            "started in the window.",
            "min",
            _case_metric("turnaround_s / 60.0"),
        ),
        MetricDef(
            "wait_for_forklift",
            "Minutes a truck was at the dock with no forklift in the zone, before its last "
            "handling, for complete visits that started in the window.",
            "min",
            _case_metric("wait_s / 60.0"),
        ),
        MetricDef(
            "handling",
            "Minutes a forklift was in the dock zone with the truck, for complete visits that "
            "started in the window.",
            "min",
            _case_metric("handling_s / 60.0"),
        ),
        MetricDef(
            "post_dwell",
            "Minutes a truck stayed after the last forklift left, for complete visits that "
            "started in the window.",
            "min",
            _case_metric("post_dwell_s / 60.0"),
        ),
        MetricDef(
            "forklift_active_share",
            "Share of the time forklifts were seen at the station that they were moving, "
            "averaged over time bins.",
            "share",
            _forklift_bins(
                f"SUM(CASE WHEN i.kind = 'forklift_active' THEN {OVERLAP} ELSE 0 END) "
                f"/ SUM({OVERLAP})"
            ),
        ),
        MetricDef(
            "concurrent_forklifts",
            "Average number of forklifts moving at the station at the same time, averaged over "
            "time bins in which a forklift was seen.",
            "forklifts",
            _forklift_bins(
                f"SUM(CASE WHEN i.kind = 'forklift_active' THEN {OVERLAP} ELSE 0 END) "
                "/ (epoch(b.b1) - epoch(b.b0))"
            ),
        ),
        MetricDef(
            "aisle_blocked_min",
            "Minutes per hour that the travel aisle serving the dock was blocked by a pallet or "
            "a parked forklift, averaged over time bins.",
            "min",
            _aisle_blocked,
        ),
        MetricDef(
            "near_miss_per_forklift_hour",
            "Near-misses at the station per forklift-hour on the station, averaged over time "
            "bins. Needs the near-miss events table.",
            "per forklift-hour",
            _near_misses,
        ),
    )
}


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)


def _single_provenance(cells: Sequence[object]) -> Provenance | None:
    kinds = {part for cell in cells if cell for part in str(cell).split(",")}
    if not kinds:
        return None
    if len(kinds) != 1:
        raise ValueError(f"rows mix provenance {sorted(kinds)}; never mixed in one number (SA §9)")
    return Provenance(kinds.pop())


def _shifts_touched(stamps: Sequence[datetime], shifts: Sequence[Shift]) -> int:
    aware = [s.replace(tzinfo=UTC) if s.tzinfo is None else s for s in stamps]
    return sum(1 for start, end in shifts if any(start <= s < end for s in aware))


def measure(
    con: duckdb.DuckDBPyConnection,
    definition: MetricDef,
    scope: Scope,
    *,
    shifts: Sequence[Shift],
    coverage: CoverageOf,
) -> MetricResult | None:
    """Run one catalogue metric.

    Args:
        con: A connection holding the process-log tables.
        definition: The metric.
        scope: Station, window and bin size.
        shifts: The shifts (or sessions or services) of the recording.
        coverage: Watched fraction for a station and window, from the coverage watchdog.

    Returns:
        The result, or None if the metric cannot be measured (no rows, or its table is missing).

    Raises:
        ValueError: If the rows mix provenance.
    """
    if definition.id == "near_miss_per_forklift_hour" and not _has_table(con, "near_miss_events"):
        return None
    query = definition.build(scope)
    if query is None:
        return None
    rows = [r for r in con.execute(query.units).fetchall() if r[0] is not None]
    provenance = _single_provenance([r[2] for r in rows])
    if not rows or provenance is None:
        return None
    values = [float(r[0]) for r in rows]
    sql = f"{query.units};\n{query.objects}"
    result_id = f"mr_{hashlib.sha256(sql.encode()).hexdigest()[:12]}"
    lo, hi = bootstrap_ci(values, seed=_seed(result_id))
    objects = {r[0] for r in con.execute(query.objects).fetchall()}
    return MetricResult(
        id=result_id,
        metric=definition.id,
        station_id=scope.station,
        value=sum(values) / len(values),
        lo=lo,
        hi=hi,
        unit=definition.unit,
        n=len(values),
        n_tracks=len(objects),
        n_shifts=_shifts_touched([r[1] for r in rows], shifts),
        coverage=coverage(scope.station, scope.t0, scope.t1),
        provenance=provenance,
        group_by=("station",),
        sql=sql,
        definition=definition.definition,
        window_start=scope.t0,
        window_end=scope.t1,
    )
