# ruff: noqa: S608 - every interpolated value is a validated station id, an integer or a timestamp
"""`AdvisorStats` for dock stations, backed by the DuckDB process log (task P4-08b).

This is what `SiteAdvisorPipeline` calls for every number on a card. Everything here is code:
queries, intervals, tests and queueing models. No model writes a figure.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb

from rulecam.advisor.metrics import (
    CATALOGUE,
    CoverageOf,
    MetricResult,
    Scope,
    Shift,
    measure,
    station_literal,
    timestamp_literal,
)
from rulecam.advisor.stats import (
    active_period_bottleneck,
    benjamini_hochberg,
    hour_of_day_baseline,
    moving_window_pvalue,
    robust_z,
)
from rulecam.advisor.whatif import QueueObservations, Scenario, WhatIf, queue_what_if
from rulecam.contracts.advisor import Change

FDR = 0.05
EPSILON = 1e-9


@dataclass(frozen=True)
class CaseRow:
    """One complete dock visit, in seconds."""

    arrived: datetime
    arrived_epoch: float
    wait_s: float
    handling_s: float
    post_dwell_s: float
    turnaround_s: float


GAP_COMPONENTS: dict[str, Callable[[CaseRow], float]] = {
    "wait_for_forklift": lambda c: c.wait_s,
    "handling": lambda c: c.handling_s,
    "post_dwell": lambda c: c.post_dwell_s,
}


def _seed(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


class DockAdvisorStats:
    """Dock-station statistics over the process-log tables in a DuckDB connection.

    Args:
        con: A connection holding `cases`, `station_intervals` and the other process-log tables.
        shifts: The shifts, sessions or services of the recording. Each counts once toward the
            minimum-shifts rule if any number rests on a case or bin inside it.
        coverage: Watched fraction for a station and window (the coverage watchdog).
        aisles_for: Aisle stations serving each dock. Defaults to every `aisle_*` station.
        forklifts: Forklifts serving the docks. Defaults to the distinct forklifts seen in the
            window.
        bin_minutes: Length of the time bins behind share and rate metrics, in set minutes.
        operating_hours_per_week: Hours the docks run in a week, to scale a what-if to a week.
        min_effect_min: A station is only flagged as a bottleneck if its mean wait in the window
            is at least this many minutes above its baseline. Waits cluster in time, so the
            significance test alone flags about one in eight quiet windows in simulation; this
            floor is what keeps a null session quiet (see the advisor README).
        seed: Seed folded into every resampling.
    """

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        *,
        shifts: Sequence[Shift],
        coverage: CoverageOf,
        aisles_for: Mapping[str, tuple[str, ...]] | None = None,
        forklifts: int | None = None,
        bin_minutes: int = 5,
        operating_hours_per_week: float = 40.0,
        min_effect_min: float = 1.0,
        seed: int = 0,
    ) -> None:
        """Store the connection and configuration."""
        self._con = con
        self._shifts = tuple(shifts)
        self._coverage = coverage
        self._aisles_for = dict(aisles_for or {})
        self._forklifts = forklifts
        self._bin_s = bin_minutes * 60
        self._weekly_hours = operating_hours_per_week
        self._min_effect_s = min_effect_min * 60.0
        self._seed = seed
        self._results: dict[str, MetricResult] = {}

    def catalogue(self) -> frozenset[str]:
        """Return the vetted metric ids."""
        return frozenset(CATALOGUE)

    def result(self, result_id: str) -> MetricResult | None:
        """Return a metric result produced earlier by id (for the Glimmer recompute)."""
        return self._results.get(result_id)

    def _aisles(self, station: str) -> tuple[str, ...]:
        if station in self._aisles_for:
            return self._aisles_for[station]
        rows = self._con.execute(
            "SELECT DISTINCT station FROM station_intervals "
            "WHERE kind = 'aisle_blocked' ORDER BY station"
        ).fetchall()
        return tuple(str(r[0]) for r in rows)

    def metric(
        self, metric_id: str, station_id: str, t0: datetime, t1: datetime
    ) -> MetricResult | None:
        """Return a catalogue metric for a station and window, or None if it cannot be measured.

        Raises:
            ValueError: If `metric_id` is not in the catalogue, or the rows mix provenance.
        """
        if metric_id not in CATALOGUE:
            raise ValueError(f"{metric_id!r} is not in the metric catalogue")
        scope = Scope(station_id, t0, t1, self._bin_s, self._aisles(station_id))
        result = measure(
            self._con, CATALOGUE[metric_id], scope, shifts=self._shifts, coverage=self._coverage
        )
        if result is not None:
            self._results[result.id] = result
        return result

    def _cases(self, station: str, t0: datetime, t1: datetime, *, inside: bool) -> list[CaseRow]:
        """Complete cases at a station, inside or outside the window."""
        window = f"arrived_ts >= {timestamp_literal(t0)} AND arrived_ts < {timestamp_literal(t1)}"
        condition = window if inside else f"NOT ({window})"
        rows = self._con.execute(
            "SELECT arrived_ts, epoch(arrived_ts), wait_s, handling_s, post_dwell_s, turnaround_s "
            f"FROM cases WHERE station = {station_literal(station)} AND complete AND {condition} "
            "ORDER BY arrived_ts, case_id"
        ).fetchall()
        return [CaseRow(*r) for r in rows]

    def detect_bottlenecks(
        self, station_ids: Sequence[str], t0: datetime, t1: datetime
    ) -> list[MetricResult]:
        """Return the stations whose waiting time is unusually high, strongest first.

        Each station's mean wait in the window is tested against the same station's other cases
        (a moving-window test that respects clustering in time), the family of tests is
        controlled with Benjamini-Hochberg,
        and flagged stations are ordered by Roser's active-period share, then by how far above
        the hour-of-day baseline the window sits.
        """
        tested: list[tuple[str, float, float, float]] = []
        for station in station_ids:
            inside = self._cases(station, t0, t1, inside=True)
            outside = self._cases(station, t0, t1, inside=False)
            if not inside or not outside:
                continue
            start = _aware(t0).timestamp()
            before = [c.wait_s for c in outside if c.arrived_epoch < start]
            after = [c.wait_s for c in outside if c.arrived_epoch >= start]
            p = moving_window_pvalue([c.wait_s for c in inside], [before, after])
            if p is not None:
                effect = _mean([c.wait_s for c in inside]) - _mean([c.wait_s for c in outside])
                tested.append((station, p, self._strength(inside, outside), effect))
        if not tested:
            return []
        decision = benjamini_hochberg([p for _, p, _, _ in tested], FDR)
        flagged = [
            (station, strength)
            for (station, _, strength, effect), hit in zip(tested, decision.rejected, strict=True)
            if hit and effect >= self._min_effect_s
        ]
        roser = self._roser_shares(station_ids, t0, t1)
        flagged.sort(key=lambda item: (-roser.get(item[0], 0.0), -item[1], item[0]))
        results = (self.metric("wait_for_forklift", s, t0, t1) for s, _ in flagged)
        return [r for r in results if r is not None]

    @staticmethod
    def _strength(
        inside: Sequence[CaseRow],
        outside: Sequence[CaseRow],
    ) -> float:
        """Robust z of the window's mean wait against the hour-of-day baseline (0 if undefined)."""
        baseline = hour_of_day_baseline([c.arrived for c in outside], [c.wait_s for c in outside])
        mean_wait = sum(c.wait_s for c in inside) / len(inside)
        scores = [
            z
            for hour in {c.arrived.hour for c in inside}
            if hour in baseline and (z := robust_z(mean_wait, baseline[hour])) is not None
        ]
        return sum(scores) / len(scores) if scores else 0.0

    def _roser_shares(
        self, station_ids: Sequence[str], t0: datetime, t1: datetime
    ) -> dict[str, float]:
        spans: dict[str, list[tuple[float, float]]] = {}
        for station in station_ids:
            rows = self._con.execute(
                "SELECT epoch(start_ts), epoch(end_ts) FROM station_intervals "
                f"WHERE kind = 'handling' AND station = {station_literal(station)} "
                f"AND start_ts < {timestamp_literal(t1)} AND end_ts > {timestamp_literal(t0)}"
            ).fetchall()
            spans[station] = [(float(a), float(b)) for a, b in rows]
        window = (_aware(t0).timestamp(), _aware(t1).timestamp())
        return active_period_bottleneck(spans, window)

    def gap_share(
        self, metric_id: str, station_id: str, t0: datetime, t1: datetime
    ) -> float | None:
        """Return the share of the turnaround gap that one component accounts for.

        The gap is the window's mean turnaround minus the same station's mean over its other
        cases. Wait, handling and post-handling dwell add up to turnaround, so their shares sum
        to 1. Metrics that are not such a component have no honest share, so they return None.
        """
        component = GAP_COMPONENTS.get(metric_id)
        if component is None:
            return None
        inside = self._cases(station_id, t0, t1, inside=True)
        outside = self._cases(station_id, t0, t1, inside=False)
        if not inside or not outside:
            return None

        def difference(value: Callable[[CaseRow], float]) -> float:
            return sum(map(value, inside)) / len(inside) - sum(map(value, outside)) / len(outside)

        total = difference(lambda c: c.turnaround_s)
        return None if abs(total) < EPSILON else difference(component) / total

    def _forklift_count(self, t0: datetime, t1: datetime) -> int:
        if self._forklifts is not None:
            return self._forklifts
        row = self._con.execute(
            "SELECT COUNT(DISTINCT object_id) FROM station_intervals "
            "WHERE kind IN ('forklift_active', 'forklift_idle') "
            f"AND start_ts < {timestamp_literal(t1)} AND end_ts > {timestamp_literal(t0)}"
        ).fetchone()
        return max(1, int(row[0])) if row else 1

    @staticmethod
    def _scenario(change: Change) -> Scenario | None:
        """Read the modelled part of a change: extra servers and/or smoother arrivals."""
        try:
            default_added = "1" if change.op == "add_capacity" else "0"
            added = int(change.params.get("servers_added", default_added))
            scale = float(change.params.get("arrival_scv_scale", "1"))
        except ValueError:
            return None
        if added < 0 or scale < 0 or (added == 0 and scale == 1.0):
            return None
        return Scenario(servers_added=added, arrival_scv_scale=scale)

    def what_if(self, change: Change, station_id: str, t0: datetime, t1: datetime) -> WhatIf | None:
        """Return the modelled gain of a change, or None if no rung's assumptions hold.

        Only changes that name `servers_added` or `arrival_scv_scale` in their params (or
        `add_capacity`, one server) can be modelled; others return None.
        """
        scenario = self._scenario(change)
        cases = self._cases(station_id, t0, t1, inside=True)
        if scenario is None or not cases:
            return None
        start = _aware(t0).timestamp()
        observations = QueueObservations(
            arrival_times_s=[c.arrived_epoch - start for c in cases],
            service_times_s=[c.handling_s for c in cases],
            observed_waits_s=[c.wait_s for c in cases],
            window_s=(_aware(t1) - _aware(t0)).total_seconds(),
            servers=self._forklift_count(t0, t1),
            bin_s=float(self._bin_s),
        )
        return queue_what_if(
            observations,
            scenario,
            seed=_seed(self._seed, station_id, t0, t1, change.op),
            operating_hours_per_week=self._weekly_hours,
        )
