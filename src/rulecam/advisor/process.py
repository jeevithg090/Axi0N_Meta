"""Site Advisor process log: station intervals, OCEL 2.0-shaped tables and quality gates (P1-12).

Tracks become an object-centric process log (SA §4, steps 3-4):

* **Stations** come from plan zones (`dock_*`, `aisle_*`; see `stations_from_zones`).
* **Objects** are trucks, pallets and forklifts only. **People are never process objects**: tracks
  whose concept is not mapped in `ProcessConfig.object_types` are dropped before anything is
  built, and the config refuses to map a person concept. Object ids are fresh (`forklift-001`,
  `truck-dock_3-002`) and never derived from the tracker's ids.
* **Dock cases**: one case per truck visit to a dock (truck presence, broken tracks bridged by
  `max_gap_s`). Each visit splits into waiting for a forklift (truck present, no forklift in the
  zone, before the last handling), handling (a forklift in the zone) and post-handling dwell
  (after the last forklift leaves), so `wait + handling + post_dwell = turnaround`.
* **Forklift activity**: active while the smoothed ground speed exceeds 0.2 m/s, idle otherwise
  (needs calibrated `foot_m`; uncalibrated points give no activity intervals).
* **Aisle blocked**: a pallet, or an idle forklift, inside an aisle zone for at least
  `aisle_min_blocked_s`.

Presence uses sample-and-hold: a point's in-zone state holds until the next point of the same
track, if that point comes within `max_hold_s`. Events at the window edges are not invented: a
visit already in progress at `t0` has no `truck_arrived`, one still in progress at `t1` has no
`handling_ended` or `truck_departed`, and both are marked incomplete.

`write_duckdb` writes the tables `objects`, `events`, `event_object` (OCEL 2.0-shaped) plus
`station_intervals` and `cases`. Timestamps are stored as UTC `TIMESTAMP`. Every row carries
`provenance`, and one log never mixes provenances.

`assess_quality` returns a `QualityReport`: watched fraction (from the coverage watchdog), a
Little's-law cross-check of occupancy from raw tracks against the case builder, the fragment rate
(tracks shorter than 2 s) and incomplete cases.
"""

from __future__ import annotations

import itertools
import math
import re
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

import duckdb
from pydantic import Field, model_validator

from rulecam.contracts import Frozen, Provenance, Track, TrackPoint, Zone
from rulecam.perception.intervals import (
    Interval,
    clip,
    intersect,
    merge,
    overlaps,
    subtract,
    total_seconds,
)

ObjectType = Literal["truck", "pallet", "forklift"]
StationKind = Literal["dock", "aisle"]
IntervalKind = Literal[
    "truck_present",
    "waiting",
    "handling",
    "post_dwell",
    "forklift_active",
    "forklift_idle",
    "aisle_blocked",
]
Activity = Literal[
    "truck_arrived",
    "handling_started",
    "handling_ended",
    "truck_departed",
    "pallet_staged",
    "aisle_blocked",
    "aisle_cleared",
]
Qualifier = Literal["truck", "handler", "pallet", "blocker"]
Link = tuple[str, Qualifier]
GateStatus = Literal["pass", "fail", "n/a"]
GateName = Literal["watched_fraction", "littles_law", "fragment_rate", "incomplete_cases"]
Point = tuple[float, float]

PERSON_CONCEPTS = frozenset(
    {"person", "people", "worker", "human", "pedestrian", "man", "woman", "child", "staff"}
)
_STATION_ID = re.compile(r"^(dock|aisle)(?:[_-]|\d|$)")
_OBJECT_TYPES: tuple[ObjectType, ...] = ("forklift", "pallet", "truck")
_MIN_POLYGON = 3


# --- configuration and inputs -----------------------------------------------------------------


def _default_object_types() -> dict[str, ObjectType]:
    return {"truck": "truck", "forklift": "forklift", "pallet": "pallet"}


class ProcessConfig(Frozen):
    """How tracks become process objects and intervals."""

    object_types: dict[str, ObjectType] = Field(default_factory=_default_object_types)
    max_hold_s: float = Field(default=1.0, gt=0)
    max_gap_s: float = Field(default=2.0, ge=0)
    active_speed_mps: float = Field(default=0.2, ge=0)
    speed_window: int = Field(default=5, ge=1)
    aisle_min_blocked_s: float = Field(default=5.0, ge=0)
    edge_tolerance_s: float = Field(default=1.0, ge=0)
    stitch_gap_s: float = Field(default=2.0, ge=0)
    stitch_dist_px: float = Field(default=60.0, ge=0)

    @model_validator(mode="after")
    def _no_people(self) -> ProcessConfig:
        people = sorted(c for c in self.object_types if c.strip().lower() in PERSON_CONCEPTS)
        if people:
            msg = f"people are never process objects; refusing to map {people}"
            raise ValueError(msg)
        return self


class Station(Frozen):
    """A dock or aisle station: a named image polygon on one camera."""

    station_id: str
    kind: StationKind
    camera_id: str
    polygon_px: tuple[Point, ...] = Field(min_length=_MIN_POLYGON)


def stations_from_zones(zones: Iterable[Zone]) -> tuple[Station, ...]:
    """Build stations from plan zones whose ids start with `dock` or `aisle`.

    Args:
        zones: Plan zones; other zones are ignored.

    Returns:
        Stations sorted by id.

    Raises:
        ValueError: If a station zone has no image polygon (e.g. a buffer zone).
    """
    stations = []
    for zone in sorted(zones, key=lambda z: z.zone_id):
        found = _STATION_ID.match(zone.zone_id)
        if found is None:
            continue
        if zone.polygon_px is None or len(zone.polygon_px) < _MIN_POLYGON:
            msg = f"station zone {zone.zone_id} needs an image polygon"
            raise ValueError(msg)
        kind: StationKind = "dock" if found.group(1) == "dock" else "aisle"
        stations.append(
            Station(
                station_id=zone.zone_id,
                kind=kind,
                camera_id=zone.camera_id,
                polygon_px=zone.polygon_px,
            )
        )
    return tuple(stations)


# --- output rows ------------------------------------------------------------------------------


class OcelObject(Frozen):
    """An OCEL object: a truck, pallet or forklift (never a person)."""

    object_id: str
    type: ObjectType
    provenance: Provenance


class OcelEvent(Frozen):
    """An OCEL event at a station."""

    event_id: str
    activity: Activity
    ts: datetime
    station: str
    provenance: Provenance


class EventObject(Frozen):
    """An OCEL event-to-object relation with its qualifier."""

    event_id: str
    object_id: str
    qualifier: Qualifier
    provenance: Provenance


class StationInterval(Frozen):
    """A derived interval at a station (or of one forklift, for activity)."""

    interval_id: str
    kind: IntervalKind
    station: str | None
    start: datetime
    end: datetime
    case_id: str | None = None
    object_id: str | None = None
    provenance: Provenance

    @property
    def seconds(self) -> float:
        """Length in seconds."""
        return (self.end - self.start).total_seconds()


class DockCase(Frozen):
    """One truck visit to a dock, split into waiting, handling and post-handling dwell."""

    case_id: str
    station: str
    truck_object_id: str
    start: datetime
    end: datetime
    wait_s: float
    handling_s: float
    post_dwell_s: float
    complete: bool
    provenance: Provenance

    @property
    def turnaround_s(self) -> float:
        """Visit length in seconds (equals wait + handling + post-handling dwell)."""
        return (self.end - self.start).total_seconds()


class ProcessLog(Frozen):
    """The process log of one window from one provenance."""

    t0: datetime
    t1: datetime
    provenance: Provenance
    objects: tuple[OcelObject, ...]
    events: tuple[OcelEvent, ...]
    event_objects: tuple[EventObject, ...]
    intervals: tuple[StationInterval, ...]
    cases: tuple[DockCase, ...]


# --- geometry and per-track spans (pure) ------------------------------------------------------


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Ray-casting point-in-polygon test (points on the boundary may go either way)."""
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in itertools.pairwise((*polygon, polygon[0])):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def runs(
    points: Sequence[TrackPoint], keep: Callable[[TrackPoint], bool], max_hold_s: float
) -> list[Interval]:
    """Spans of time during which `keep` holds for one track, with sample-and-hold.

    A point's state holds until the next point if that comes within `max_hold_s`; otherwise the
    span ends at the last point that kept it.

    Args:
        points: One track's points, sorted by time.
        keep: The per-point condition.
        max_hold_s: Longest gap between points that still counts as continuous.

    Returns:
        Sorted spans with positive length.
    """
    spans: list[Interval] = []
    start: datetime | None = None
    last: TrackPoint | None = None
    for point in points:
        hit = keep(point)
        contiguous = last is not None and (point.ts - last.ts).total_seconds() <= max_hold_s
        if start is not None and last is not None and not (hit and contiguous):
            spans.append(Interval(start=start, end=point.ts if contiguous else last.ts))
            start = None
        if hit and start is None:
            start = point.ts
        last = point
    if start is not None and last is not None:
        spans.append(Interval(start=start, end=last.ts))
    return [s for s in spans if s.seconds > 0]


def in_zone(points: Sequence[TrackPoint], station: Station, max_hold_s: float) -> list[Interval]:
    """Spans during which a track's foot point is inside the station polygon."""
    return runs(points, lambda p: point_in_polygon(p.foot_px, station.polygon_px), max_hold_s)


def _motion_runs(
    points: Sequence[TrackPoint], max_hold_s: float
) -> list[list[tuple[Interval, float]]]:
    """Group consecutive calibrated steps of one track into runs of (step, ground speed)."""
    groups: list[list[tuple[Interval, float]]] = []
    for prev, cur in itertools.pairwise(points):
        dt = (cur.ts - prev.ts).total_seconds()
        if prev.foot_m is None or cur.foot_m is None or not 0 < dt <= max_hold_s:
            continue
        step = (Interval(start=prev.ts, end=cur.ts), math.dist(prev.foot_m, cur.foot_m) / dt)
        if groups and groups[-1][-1][0].end == prev.ts:
            groups[-1].append(step)
        else:
            groups.append([step])
    return groups


def activity(
    points: Sequence[TrackPoint], config: ProcessConfig
) -> tuple[list[Interval], list[Interval]]:
    """Active and idle spans of one forklift from its median-smoothed ground speed.

    Args:
        points: One forklift's points, sorted by time.
        config: Process configuration (speed threshold, smoothing window, hold).

    Returns:
        `(active, idle)` spans.
    """
    active: list[Interval] = []
    idle: list[Interval] = []
    half = config.speed_window // 2
    for group in _motion_runs(points, config.max_hold_s):
        speeds = [speed for _, speed in group]
        for k, (step, _) in enumerate(group):
            smoothed = statistics.median(speeds[max(0, k - half) : k + half + 1])
            (active if smoothed > config.active_speed_mps else idle).append(step)
    return merge(active), merge(idle)


# --- objects: filtering and stitching ---------------------------------------------------------


@dataclass(frozen=True)
class _Piece:
    """One track restricted to the window (internal; the tracker id is deliberately dropped)."""

    object_type: ObjectType
    camera_id: str
    points: tuple[TrackPoint, ...]


@dataclass(frozen=True)
class _Body:
    """One stitched physical object with its process-log id."""

    object_id: str
    object_type: ObjectType
    camera_id: str
    points: tuple[TrackPoint, ...]


def _pieces(tracks: Iterable[Track], window: Interval, config: ProcessConfig) -> list[_Piece]:
    """Keep tracks of mapped object concepts only (never people), clipped to the window."""
    pieces = []
    for track in tracks:
        object_type = config.object_types.get(track.concept)
        if object_type is None:
            continue
        points = tuple(
            sorted((p for p in track.points if window.start <= p.ts <= window.end), key=_ts)
        )
        if points:
            pieces.append(_Piece(object_type, track.camera_id, points))
    return pieces


def _ts(point: TrackPoint) -> datetime:
    return point.ts


def _continues(group: tuple[str, list[TrackPoint]], piece: _Piece, config: ProcessConfig) -> bool:
    camera_id, points = group
    last, first = points[-1], piece.points[0]
    gap = (first.ts - last.ts).total_seconds()
    return (
        camera_id == piece.camera_id
        and 0 < gap <= config.stitch_gap_s
        and math.dist(last.foot_px, first.foot_px) <= config.stitch_dist_px
    )


def _stitch(pieces: Sequence[_Piece], config: ProcessConfig) -> list[tuple[str, list[TrackPoint]]]:
    """Join a piece to an earlier one of the same camera that ended just before, close by."""
    groups: list[tuple[str, list[TrackPoint]]] = []
    ordered = sorted(pieces, key=lambda p: (p.points[0].ts, p.camera_id, p.points[0].foot_px))
    for piece in ordered:
        target = next((g for g in groups if _continues(g, piece, config)), None)
        if target is None:
            groups.append((piece.camera_id, list(piece.points)))
        else:
            target[1].extend(piece.points)
    return groups


def _bodies(pieces: Sequence[_Piece], config: ProcessConfig) -> list[_Body]:
    bodies = []
    for object_type in _OBJECT_TYPES:
        groups = _stitch([p for p in pieces if p.object_type == object_type], config)
        for n, (camera_id, points) in enumerate(groups, start=1):
            bodies.append(_Body(f"{object_type}-{n:03d}", object_type, camera_id, tuple(points)))
    return bodies


# --- building the log -------------------------------------------------------------------------


@dataclass(frozen=True)
class _EventDraft:
    ts: datetime
    activity: Activity
    station: str
    links: tuple[Link, ...]


@dataclass(frozen=True)
class _IntervalDraft:
    kind: IntervalKind
    station: str | None
    span: Interval
    case_id: str | None = None
    object_id: str | None = None


@dataclass
class _Draft:
    window: Interval
    config: ProcessConfig
    provenance: Provenance
    events: list[_EventDraft] = field(default_factory=list)
    intervals: list[_IntervalDraft] = field(default_factory=list)
    cases: list[DockCase] = field(default_factory=list)

    def left_censored(self, span: Interval) -> bool:
        return (span.start - self.window.start).total_seconds() <= self.config.edge_tolerance_s

    def right_censored(self, span: Interval) -> bool:
        return (self.window.end - span.end).total_seconds() <= self.config.edge_tolerance_s

    def event(
        self, ts: datetime, activity: Activity, station: str, links: tuple[Link, ...]
    ) -> None:
        self.events.append(_EventDraft(ts, activity, station, links))


def _bridge(spans: Iterable[Interval], config: ProcessConfig) -> list[Interval]:
    return [s for s in merge(spans, config.max_gap_s) if s.seconds > 0]


def _phases(visit: Interval, handling: list[Interval]) -> tuple[list[Interval], list[Interval]]:
    """Split a visit into (waiting, post-handling dwell) around its handling spans."""
    if not handling:
        return [visit], []
    last = handling[-1].end
    waiting = subtract([Interval(start=visit.start, end=last)], handling)
    post = [Interval(start=last, end=visit.end)] if last < visit.end else []
    return waiting, post


def _links(ids: Iterable[str], qualifier: Qualifier) -> tuple[Link, ...]:
    return tuple((object_id, qualifier) for object_id in ids)


def _touching(spans_by_object: dict[str, list[Interval]], span: Interval) -> list[str]:
    """Ids of the objects with at least one span overlapping `span`, sorted."""
    return [
        object_id
        for object_id, spans in sorted(spans_by_object.items())
        if any(overlaps(s, span) for s in spans)
    ]


def _zone_spans(
    bodies: Iterable[_Body], station: Station, object_type: ObjectType, max_hold_s: float
) -> dict[str, list[Interval]]:
    return {
        b.object_id: in_zone(b.points, station, max_hold_s)
        for b in bodies
        if b.camera_id == station.camera_id and b.object_type == object_type
    }


def _add_dock(draft: _Draft, station: Station, bodies: Sequence[_Body]) -> None:
    """Record every truck visit at one dock: case, phase intervals and lifecycle events."""
    hold = draft.config.max_hold_s
    trucks = _zone_spans(bodies, station, "truck", hold)
    forklifts = _zone_spans(bodies, station, "forklift", hold)
    pallets = _zone_spans(bodies, station, "pallet", hold)
    handling_all = _bridge(itertools.chain.from_iterable(forklifts.values()), draft.config)
    visits = _bridge(itertools.chain.from_iterable(trucks.values()), draft.config)
    for k, visit in enumerate(visits, start=1):
        case_id = f"case-{station.station_id}-{k:03d}"
        truck_id = f"truck-{station.station_id}-{k:03d}"
        handling = clip(handling_all, visit)
        _add_case(
            draft,
            station.station_id,
            case_id=case_id,
            truck_id=truck_id,
            visit=visit,
            handling=handling,
        )
        links = ((truck_id, "truck"), *_links(_touching(forklifts, visit), "handler"))
        _add_visit_events(draft, station.station_id, visit, handling, links)
        for pallet_id, spans in sorted(pallets.items()):
            for span in spans:
                if visit.start <= span.start < visit.end:
                    staged: tuple[Link, ...] = ((pallet_id, "pallet"), (truck_id, "truck"))
                    draft.event(span.start, "pallet_staged", station.station_id, staged)


def _add_case(
    draft: _Draft,
    station_id: str,
    *,
    case_id: str,
    truck_id: str,
    visit: Interval,
    handling: list[Interval],
) -> None:
    waiting, post = _phases(visit, handling)
    draft.cases.append(
        DockCase(
            case_id=case_id,
            station=station_id,
            truck_object_id=truck_id,
            start=visit.start,
            end=visit.end,
            wait_s=total_seconds(waiting),
            handling_s=total_seconds(handling),
            post_dwell_s=total_seconds(post),
            complete=not (draft.left_censored(visit) or draft.right_censored(visit)),
            provenance=draft.provenance,
        )
    )
    phases: tuple[tuple[IntervalKind, list[Interval]], ...] = (
        ("truck_present", [visit]),
        ("waiting", waiting),
        ("handling", handling),
        ("post_dwell", post),
    )
    for kind, spans in phases:
        draft.intervals.extend(_IntervalDraft(kind, station_id, s, case_id) for s in spans)


def _add_visit_events(
    draft: _Draft,
    station_id: str,
    visit: Interval,
    handling: list[Interval],
    links: tuple[Link, ...],
) -> None:
    """Lifecycle events of one visit; nothing is invented at the window edges."""
    left, right = draft.left_censored(visit), draft.right_censored(visit)
    truck = links[:1]
    if not left:
        draft.event(visit.start, "truck_arrived", station_id, truck)
    if handling and (not left or handling[0].start > visit.start):
        draft.event(handling[0].start, "handling_started", station_id, links)
    if handling and not right:
        draft.event(handling[-1].end, "handling_ended", station_id, links)
    if not right:
        draft.event(visit.end, "truck_departed", station_id, truck)


def _add_aisle(draft: _Draft, station: Station, bodies: Sequence[_Body]) -> None:
    """Record aisle-blocked intervals: a pallet, or an idle forklift, in the aisle zone."""
    cfg = draft.config
    blockers = _zone_spans(bodies, station, "pallet", cfg.max_hold_s)
    for body in bodies:
        if body.camera_id == station.camera_id and body.object_type == "forklift":
            idle_inside = intersect(
                in_zone(body.points, station, cfg.max_hold_s), activity(body.points, cfg)[1]
            )
            blockers[body.object_id] = idle_inside
    merged = _bridge(itertools.chain.from_iterable(blockers.values()), cfg)
    for span in (s for s in merged if s.seconds >= cfg.aisle_min_blocked_s):
        links = _links(_touching(blockers, span), "blocker")
        draft.intervals.append(_IntervalDraft("aisle_blocked", station.station_id, span))
        if not draft.left_censored(span):
            draft.event(span.start, "aisle_blocked", station.station_id, links)
        if not draft.right_censored(span):
            draft.event(span.end, "aisle_cleared", station.station_id, links)


def _add_activity(draft: _Draft, bodies: Sequence[_Body]) -> None:
    for body in bodies:
        if body.object_type != "forklift":
            continue
        active, idle = activity(body.points, draft.config)
        spans_by_kind: tuple[tuple[IntervalKind, list[Interval]], ...] = (
            ("forklift_active", active),
            ("forklift_idle", idle),
        )
        for kind, spans in spans_by_kind:
            draft.intervals.extend(
                _IntervalDraft(kind, None, s, object_id=body.object_id) for s in spans
            )


def _interval_order(item: _IntervalDraft) -> tuple[datetime, datetime, str, str, str]:
    return (item.span.start, item.span.end, item.kind, item.station or "", item.object_id or "")


def _finish(draft: _Draft, bodies: Sequence[_Body]) -> ProcessLog:
    """Number events and intervals deterministically and assemble the log."""
    prov = draft.provenance
    events: list[OcelEvent] = []
    relations: list[EventObject] = []
    ordered = sorted(draft.events, key=lambda e: (e.ts, e.station, e.activity, e.links))
    for n, item in enumerate(ordered, start=1):
        event_id = f"ev-{n:05d}"
        events.append(
            OcelEvent(
                event_id=event_id,
                activity=item.activity,
                ts=item.ts,
                station=item.station,
                provenance=prov,
            )
        )
        relations.extend(
            EventObject(event_id=event_id, object_id=oid, qualifier=q, provenance=prov)
            for oid, q in item.links
        )
    intervals = tuple(
        StationInterval(
            interval_id=f"iv-{n:05d}",
            kind=item.kind,
            station=item.station,
            start=item.span.start,
            end=item.span.end,
            case_id=item.case_id,
            object_id=item.object_id,
            provenance=prov,
        )
        for n, item in enumerate(sorted(draft.intervals, key=_interval_order), start=1)
    )
    objects = [
        OcelObject(object_id=c.truck_object_id, type="truck", provenance=prov) for c in draft.cases
    ]
    objects += [
        OcelObject(object_id=b.object_id, type=b.object_type, provenance=prov)
        for b in bodies
        if b.object_type != "truck"
    ]
    return ProcessLog(
        t0=draft.window.start,
        t1=draft.window.end,
        provenance=prov,
        objects=tuple(sorted(objects, key=lambda o: o.object_id)),
        events=tuple(events),
        event_objects=tuple(relations),
        intervals=intervals,
        cases=tuple(sorted(draft.cases, key=lambda c: (c.start, c.station))),
    )


def _window(t0: datetime, t1: datetime) -> Interval:
    if t1 <= t0:
        msg = f"window end {t1} must be after start {t0}"
        raise ValueError(msg)
    return Interval(start=t0, end=t1)


def build_process_log(
    tracks: Sequence[Track],
    stations: Sequence[Station],
    *,
    t0: datetime,
    t1: datetime,
    provenance: Provenance,
    config: ProcessConfig | None = None,
) -> ProcessLog:
    """Build the station process log of one window.

    Pure: the same inputs always give the same log, whatever the order of `tracks`.

    Args:
        tracks: Tracks of the window, typically `TrackStore.window(...)` per station camera.
            Person tracks (and any concept not in `config.object_types`) are ignored.
        stations: Dock and aisle stations.
        t0: Window start.
        t1: Window end.
        provenance: Where the tracks came from; every row carries it.
        config: Process configuration.

    Returns:
        The process log.

    Raises:
        ValueError: If the window is empty or two stations share an id.
    """
    cfg = config or ProcessConfig()
    ids = [s.station_id for s in stations]
    if len(set(ids)) != len(ids):
        msg = f"station ids must be unique: {sorted(ids)}"
        raise ValueError(msg)
    draft = _Draft(window=_window(t0, t1), config=cfg, provenance=provenance)
    bodies = _bodies(_pieces(tracks, draft.window, cfg), cfg)
    for station in sorted(stations, key=lambda s: s.station_id):
        if station.kind == "dock":
            _add_dock(draft, station, bodies)
        else:
            _add_aisle(draft, station, bodies)
    _add_activity(draft, bodies)
    return _finish(draft, bodies)


# --- DuckDB -----------------------------------------------------------------------------------

_DDL = (
    "CREATE OR REPLACE TABLE objects ("
    "object_id VARCHAR PRIMARY KEY, type VARCHAR NOT NULL, provenance VARCHAR NOT NULL)",
    "CREATE OR REPLACE TABLE events ("
    "event_id VARCHAR PRIMARY KEY, activity VARCHAR NOT NULL, ts TIMESTAMP NOT NULL, "
    "station VARCHAR NOT NULL, provenance VARCHAR NOT NULL)",
    "CREATE OR REPLACE TABLE event_object ("
    "event_id VARCHAR NOT NULL, object_id VARCHAR NOT NULL, qualifier VARCHAR NOT NULL, "
    "provenance VARCHAR NOT NULL)",
    "CREATE OR REPLACE TABLE station_intervals ("
    "interval_id VARCHAR PRIMARY KEY, kind VARCHAR NOT NULL, station VARCHAR, "
    "start_ts TIMESTAMP NOT NULL, end_ts TIMESTAMP NOT NULL, seconds DOUBLE NOT NULL, "
    "case_id VARCHAR, object_id VARCHAR, provenance VARCHAR NOT NULL)",
    "CREATE OR REPLACE TABLE cases ("
    "case_id VARCHAR PRIMARY KEY, station VARCHAR NOT NULL, truck_object_id VARCHAR NOT NULL, "
    "arrived_ts TIMESTAMP NOT NULL, departed_ts TIMESTAMP NOT NULL, wait_s DOUBLE NOT NULL, "
    "handling_s DOUBLE NOT NULL, post_dwell_s DOUBLE NOT NULL, turnaround_s DOUBLE NOT NULL, "
    "complete BOOLEAN NOT NULL, provenance VARCHAR NOT NULL)",
)
_INSERT = {
    "objects": "INSERT INTO objects VALUES (?, ?, ?)",
    "events": "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
    "event_object": "INSERT INTO event_object VALUES (?, ?, ?, ?)",
    "station_intervals": "INSERT INTO station_intervals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    "cases": "INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
}
PROCESS_TABLES = tuple(_INSERT)


def _utc(ts: datetime) -> datetime:
    """Naive UTC datetime for a DuckDB `TIMESTAMP` column (naive input is taken as UTC)."""
    return ts if ts.tzinfo is None else ts.astimezone(UTC).replace(tzinfo=None)


def _rows(log: ProcessLog) -> dict[str, list[tuple[object, ...]]]:
    return {
        "objects": [(o.object_id, o.type, o.provenance.value) for o in log.objects],
        "events": [
            (e.event_id, e.activity, _utc(e.ts), e.station, e.provenance.value) for e in log.events
        ],
        "event_object": [
            (r.event_id, r.object_id, r.qualifier, r.provenance.value) for r in log.event_objects
        ],
        "station_intervals": [
            (
                i.interval_id,
                i.kind,
                i.station,
                _utc(i.start),
                _utc(i.end),
                i.seconds,
                i.case_id,
                i.object_id,
                i.provenance.value,
            )
            for i in log.intervals
        ],
        "cases": [
            (
                c.case_id,
                c.station,
                c.truck_object_id,
                _utc(c.start),
                _utc(c.end),
                c.wait_s,
                c.handling_s,
                c.post_dwell_s,
                c.turnaround_s,
                c.complete,
                c.provenance.value,
            )
            for c in log.cases
        ],
    }


def write_duckdb(log: ProcessLog, con: duckdb.DuckDBPyConnection) -> None:
    """Create (or replace) the process-log tables in `con` and insert every row.

    Args:
        log: The process log.
        con: An open DuckDB connection.
    """
    for statement in _DDL:
        con.execute(statement)
    for table, rows in _rows(log).items():
        if rows:
            con.executemany(_INSERT[table], rows)


# --- quality gates ----------------------------------------------------------------------------


class WatchedFraction(Protocol):
    """Anything that reports watched time per camera, e.g. `CoverageWatchdog`."""

    def watched_fraction(self, camera_id: str, t0: datetime, t1: datetime) -> float:
        """Return the fraction of `[t0, t1)` during which the camera was watched."""
        ...


class QualityThresholds(Frozen):
    """Pass thresholds for the quality gates."""

    min_watched_fraction: float = Field(default=0.8, ge=0, le=1)
    littles_tolerance: float = Field(default=0.15, gt=0)
    fragment_min_s: float = Field(default=2.0, gt=0)
    max_fragment_rate: float = Field(default=0.10, ge=0, le=1)
    max_incomplete_share: float = Field(default=0.25, ge=0, le=1)


class QualityGate(Frozen):
    """One quality gate's measured value, threshold and status."""

    name: GateName
    value: float | None
    threshold: float
    status: GateStatus
    detail: str = ""


class LittleCheck(Frozen):
    """Little's law: occupancy L from raw tracks against lambda * W from the case builder."""

    occupancy: float  # mean trucks present at the docks (L)
    arrival_rate_per_h: float  # cases per hour (lambda)
    mean_turnaround_s: float | None  # W
    lambda_w: float
    rel_error: float | None  # |L - lambda W| / max(L, lambda W)
    status: GateStatus


class QualityReport(Frozen):
    """Quality gates of one process log; failed gates downgrade or hide a finding (SA §4)."""

    t0: datetime
    t1: datetime
    provenance: Provenance
    watched_by_camera: dict[str, float]
    little: LittleCheck
    fragment_rate: float | None
    n_tracks: int
    n_cases: int
    n_incomplete: int
    gates: tuple[QualityGate, ...]

    @property
    def passed(self) -> bool:
        """True when no gate failed (gates that do not apply are `n/a`)."""
        return all(g.status != "fail" for g in self.gates)

    def gate(self, name: GateName) -> QualityGate:
        """Return the gate called `name`."""
        return next(g for g in self.gates if g.name == name)


def _status(ok: bool | None) -> GateStatus:
    if ok is None:
        return "n/a"
    return "pass" if ok else "fail"


def littles_law_check(
    log: ProcessLog,
    tracks: Sequence[Track],
    stations: Sequence[Station],
    *,
    config: ProcessConfig | None = None,
    tolerance: float = 0.15,
) -> LittleCheck:
    """Cross-check dock occupancy from raw truck tracks against the case builder.

    L is the time-average number of truck tracks inside dock zones, computed from the raw
    (unstitched, unbridged) tracks. lambda is cases per second and W the mean case turnaround, both
    from `log.cases`. Duplicated or overlapping truck tracks inflate L; over-eager bridging or
    dropped visits change lambda * W. The check passes when the relative error is within
    `tolerance`.

    Args:
        log: The process log built from `tracks`.
        tracks: The same tracks the log was built from.
        stations: The same stations.
        config: The same process configuration.
        tolerance: Relative tolerance (0.15 = 15%).

    Returns:
        The check, `n/a` when there are neither trucks nor cases.
    """
    cfg = config or ProcessConfig()
    window = Interval(start=log.t0, end=log.t1)
    trucks = [p for p in _pieces(tracks, window, cfg) if p.object_type == "truck"]
    docks = [s for s in stations if s.kind == "dock"]
    present_s = sum(
        total_seconds(in_zone(p.points, dock, cfg.max_hold_s))
        for dock in docks
        for p in trucks
        if p.camera_id == dock.camera_id
    )
    occupancy = present_s / window.seconds
    turnarounds = [c.turnaround_s for c in log.cases]
    mean_w = statistics.fmean(turnarounds) if turnarounds else None
    rate = len(turnarounds) / window.seconds
    lambda_w = rate * (mean_w or 0.0)
    worst = max(occupancy, lambda_w)
    rel_error = None if worst == 0 else abs(occupancy - lambda_w) / worst
    return LittleCheck(
        occupancy=occupancy,
        arrival_rate_per_h=rate * 3600.0,
        mean_turnaround_s=mean_w,
        lambda_w=lambda_w,
        rel_error=rel_error,
        status=_status(None if rel_error is None else rel_error <= tolerance),
    )


def fragment_rate(
    tracks: Sequence[Track],
    cameras: Iterable[str],
    window: Interval,
    *,
    config: ProcessConfig | None = None,
    min_s: float = 2.0,
) -> tuple[float | None, int]:
    """Share of process-object tracks on `cameras` shorter than `min_s` inside the window.

    Args:
        tracks: Tracks of the window.
        cameras: Cameras to count.
        window: The window.
        config: Process configuration (which concepts are process objects).
        min_s: Tracks shorter than this are fragments.

    Returns:
        `(rate, n_tracks)`; the rate is None when there are no tracks.
    """
    wanted = set(cameras)
    pieces = [
        p for p in _pieces(tracks, window, config or ProcessConfig()) if p.camera_id in wanted
    ]
    if not pieces:
        return None, 0
    short = sum((p.points[-1].ts - p.points[0].ts).total_seconds() < min_s for p in pieces)
    return short / len(pieces), len(pieces)


def assess_quality(
    log: ProcessLog,
    tracks: Sequence[Track],
    stations: Sequence[Station],
    *,
    coverage: WatchedFraction,
    config: ProcessConfig | None = None,
    thresholds: QualityThresholds | None = None,
) -> QualityReport:
    """Run the quality gates on a process log.

    Args:
        log: The process log.
        tracks: The tracks it was built from.
        stations: Its stations.
        coverage: Watched time per camera (the coverage watchdog).
        config: The process configuration used to build the log.
        thresholds: Gate thresholds.

    Returns:
        The quality report, with the log's provenance.
    """
    limits = thresholds or QualityThresholds()
    window = Interval(start=log.t0, end=log.t1)
    cameras = sorted({s.camera_id for s in stations})
    watched = {c: coverage.watched_fraction(c, log.t0, log.t1) for c in cameras}
    little = littles_law_check(
        log, tracks, stations, config=config, tolerance=limits.littles_tolerance
    )
    fragments, n_tracks = fragment_rate(
        tracks, cameras, window, config=config, min_s=limits.fragment_min_s
    )
    n_incomplete = sum(not c.complete for c in log.cases)
    share = n_incomplete / len(log.cases) if log.cases else None
    worst_watched = min(watched.values()) if watched else None
    gates = (
        QualityGate(
            name="watched_fraction",
            value=worst_watched,
            threshold=limits.min_watched_fraction,
            status=_status(
                None if worst_watched is None else worst_watched >= limits.min_watched_fraction
            ),
            detail="lowest watched fraction over the station cameras",
        ),
        QualityGate(
            name="littles_law",
            value=little.rel_error,
            threshold=limits.littles_tolerance,
            status=little.status,
            detail="relative error between L and lambda * W",
        ),
        QualityGate(
            name="fragment_rate",
            value=fragments,
            threshold=limits.max_fragment_rate,
            status=_status(None if fragments is None else fragments <= limits.max_fragment_rate),
            detail=f"process-object tracks shorter than {limits.fragment_min_s:g} s",
        ),
        QualityGate(
            name="incomplete_cases",
            value=share,
            threshold=limits.max_incomplete_share,
            status=_status(None if share is None else share <= limits.max_incomplete_share),
            detail="share of cases cut by the window edges",
        ),
    )
    return QualityReport(
        t0=log.t0,
        t1=log.t1,
        provenance=log.provenance,
        watched_by_camera=watched,
        little=little,
        fragment_rate=fragments,
        n_tracks=n_tracks,
        n_cases=len(log.cases),
        n_incomplete=n_incomplete,
        gates=gates,
    )
