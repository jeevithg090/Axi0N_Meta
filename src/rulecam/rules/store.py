"""`ParquetTrackStore`: the persistent `TrackStore` behind the rules engine (task P1-04).

Layout: one Parquet file per `append` and partition, under
`<root>/camera_id=<camera>/date=<YYYY-MM-DD>/part-<uuid>.parquet`, one row per track point:

    track_id, camera_id, concept, ts, frame_idx, x1, y1, x2, y2, foot_px_x, foot_px_y,
    foot_m_x, foot_m_y, conf, keypoints_json, provenance

DuckDB exposes two views over the files:

- `track_points`: every row.
- `tracks`: one row per track (`track_id, camera_id, concept`), a catalogue only.

These are the only views allowed to carry `track_id` (`PERCEPTION_INTERNAL_VIEWS`). They feed the
pure engine; any advisor-facing view must aggregate by station or zone and never expose or group by
`track_id` (rule R13, enforced in `tests/guardrails/test_track_id_views.py`). Everything is on disk,
so a new store opened on the same root sees the same data after a restart.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Self, TypedDict
from uuid import uuid4

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from rulecam.contracts import Keypoint, Provenance, Track, TrackPoint
from rulecam.log import get_logger

_log = get_logger(__name__)

TRACK_POINTS_VIEW = "track_points"
TRACKS_VIEW = "tracks"
PERCEPTION_INTERNAL_VIEWS = frozenset({TRACK_POINTS_VIEW, TRACKS_VIEW})

SCHEMA = pa.schema(
    [
        ("track_id", pa.string()),
        ("camera_id", pa.string()),
        ("concept", pa.string()),
        ("ts", pa.timestamp("us", tz="UTC")),
        ("frame_idx", pa.int64()),
        ("x1", pa.float64()),
        ("y1", pa.float64()),
        ("x2", pa.float64()),
        ("y2", pa.float64()),
        ("foot_px_x", pa.float64()),
        ("foot_px_y", pa.float64()),
        ("foot_m_x", pa.float64()),
        ("foot_m_y", pa.float64()),
        ("conf", pa.float64()),
        ("keypoints_json", pa.string()),
        ("provenance", pa.string()),
    ]
)
COLUMNS: tuple[str, ...] = tuple(SCHEMA.names)

_CAMERA_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_PARTITION_DATE = re.compile(r"date=(\d{4}-\d{2}-\d{2})")
_EMPTY_TABLE = "_empty_track_points"


class Row(TypedDict):
    """One stored track point (the Parquet schema as a typed mapping)."""

    track_id: str
    camera_id: str
    concept: str
    ts: datetime
    frame_idx: int
    x1: float
    y1: float
    x2: float
    y2: float
    foot_px_x: float
    foot_px_y: float
    foot_m_x: float | None
    foot_m_y: float | None
    conf: float | None
    keypoints_json: str | None
    provenance: str


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _check_camera_id(camera_id: str) -> str:
    if not _CAMERA_ID.fullmatch(camera_id):
        msg = f"camera_id {camera_id!r} is not safe as a partition name"
        raise ValueError(msg)
    return camera_id


def point_row(track: Track, point: TrackPoint, provenance: Provenance) -> Row:
    """Flatten one track point into a store row.

    Args:
        track: The point's track.
        point: The point.
        provenance: Provenance recorded with the row.

    Returns:
        A mapping with exactly `COLUMNS` as keys.

    Raises:
        ValueError: If the point's timestamp is naive.
    """
    x1, y1, x2, y2 = point.box_xyxy
    foot_m = point.foot_m
    keypoints = (
        json.dumps([k.model_dump() for k in point.keypoints], separators=(",", ":"))
        if point.keypoints is not None
        else None
    )
    return {
        "track_id": track.track_id,
        "camera_id": track.camera_id,
        "concept": track.concept,
        "ts": _require_aware(point.ts, "TrackPoint.ts"),
        "frame_idx": point.frame_idx,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "foot_px_x": point.foot_px[0],
        "foot_px_y": point.foot_px[1],
        "foot_m_x": foot_m[0] if foot_m is not None else None,
        "foot_m_y": foot_m[1] if foot_m is not None else None,
        "conf": point.confidence,
        "keypoints_json": keypoints,
        "provenance": str(provenance),
    }


def row_point(row: Row) -> TrackPoint:
    """Rebuild a `TrackPoint` from a store row (the inverse of `point_row`)."""
    foot_m_x, foot_m_y = row["foot_m_x"], row["foot_m_y"]
    keypoints_json = row["keypoints_json"]
    return TrackPoint(
        ts=row["ts"].astimezone(UTC),
        frame_idx=row["frame_idx"],
        box_xyxy=(row["x1"], row["y1"], row["x2"], row["y2"]),
        foot_px=(row["foot_px_x"], row["foot_px_y"]),
        foot_m=(foot_m_x, foot_m_y) if foot_m_x is not None and foot_m_y is not None else None,
        confidence=row["conf"],
        keypoints=(
            tuple(Keypoint.model_validate(k) for k in json.loads(keypoints_json))
            if keypoints_json is not None
            else None
        ),
    )


def rows_to_tracks(rows: Iterable[Row]) -> list[Track]:
    """Assemble rows into `Track`s, points sorted by time, tracks by first point then ID.

    Args:
        rows: Store rows in any order.

    Returns:
        One `Track` per track ID present in `rows`.
    """
    meta: dict[str, tuple[str, str]] = {}
    points: dict[str, list[TrackPoint]] = {}
    for row in rows:
        track_id = row["track_id"]
        meta.setdefault(track_id, (row["camera_id"], row["concept"]))
        points.setdefault(track_id, []).append(row_point(row))
    tracks = [
        Track(
            track_id=track_id,
            camera_id=meta[track_id][0],
            concept=meta[track_id][1],
            points=tuple(sorted(pts, key=lambda p: (p.ts, p.frame_idx))),
        )
        for track_id, pts in points.items()
    ]
    return sorted(tracks, key=lambda t: (t.points[0].ts, t.track_id))


class ParquetTrackStore:
    """A `TrackStore` on partitioned Parquet files, queried through DuckDB views."""

    def __init__(self, root: Path, *, provenance: Provenance = Provenance.RECORDED) -> None:
        """Open (or create) a store.

        Args:
            root: Directory holding the partitions; created if missing.
            provenance: Provenance written with every appended row.
        """
        self._root = root
        self._provenance = provenance
        root.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect()
        self._con.execute("SET TimeZone = 'UTC'")
        self._con.register(_EMPTY_TABLE, SCHEMA.empty_table())
        self._refresh_views()

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """The DuckDB connection with `track_points` and `tracks` views (for the engine)."""
        return self._con

    def close(self) -> None:
        """Close the DuckDB connection."""
        self._con.close()

    def __enter__(self) -> Self:
        """Return the store for use in a `with` block."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the store."""
        self.close()

    def append(self, tracks: list[Track]) -> None:
        """Persist the points of `tracks`, one file per camera and date.

        Args:
            tracks: Tracks to write; tracks without points are ignored.

        Raises:
            ValueError: If a camera ID is unsafe as a path, or a timestamp is naive.
        """
        partitions: dict[tuple[str, date], list[Row]] = {}
        for track in tracks:
            camera_id = _check_camera_id(track.camera_id)
            for point in track.points:
                row = point_row(track, point, self._provenance)
                day = row["ts"].date()
                partitions.setdefault((camera_id, day), []).append(row)
        for (camera_id, day), rows in sorted(partitions.items()):
            self._write(camera_id, day, rows)
        if partitions:
            self._refresh_views()
            _log.info("tracks_appended", n_tracks=len(tracks), n_files=len(partitions))

    def window(self, camera_id: str, t0: datetime, t1: datetime) -> list[Track]:
        """Return tracks of `camera_id` with only their points in `[t0, t1]` (inclusive).

        Args:
            camera_id: The camera.
            t0: Window start (timezone-aware).
            t1: Window end (timezone-aware).

        Returns:
            Tracks with at least one point in the window, sorted by first point then ID.

        Raises:
            ValueError: If `t0` or `t1` is naive.
        """
        start = _require_aware(t0, "t0")
        end = _require_aware(t1, "t1")
        if start > end:
            return []
        query = (
            f"SELECT {', '.join(COLUMNS)} FROM {TRACK_POINTS_VIEW} "  # noqa: S608 - constant names
            "WHERE camera_id = $camera AND ts >= $t0 AND ts <= $t1"
        )
        table = self._con.sql(query, params={"camera": camera_id, "t0": start, "t1": end})
        rows: list[Row] = table.to_arrow_table().to_pylist()
        return rows_to_tracks(rows)

    def apply_retention(self, retention_days: int, now: datetime) -> int:
        """Delete whole-day partitions older than the plan's retention.

        Args:
            retention_days: `SitePlan.retention_days`.
            now: The current time (from the injected clock).

        Returns:
            How many partition directories were deleted.
        """
        cutoff = (_require_aware(now, "now") - timedelta(days=retention_days)).date()
        removed = 0
        for partition in self._partitions():
            found = _PARTITION_DATE.fullmatch(partition.name)
            if found and date.fromisoformat(found.group(1)) < cutoff:
                shutil.rmtree(partition)
                removed += 1
        if removed:
            self._refresh_views()
            _log.info("tracks_retention_applied", removed=removed, cutoff=cutoff.isoformat())
        return removed

    # --- internals ------------------------------------------------------------------------------

    def _partitions(self) -> Iterator[Path]:
        for camera_dir in sorted(self._root.glob("camera_id=*")):
            yield from sorted(p for p in camera_dir.glob("date=*") if p.is_dir())

    def _write(self, camera_id: str, day: date, rows: list[Row]) -> None:
        directory = self._root / f"camera_id={camera_id}" / f"date={day.isoformat()}"
        directory.mkdir(parents=True, exist_ok=True)
        name = f"part-{uuid4().hex}.parquet"
        tmp = directory / f".{name}.tmp"
        pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp)
        tmp.replace(directory / name)

    def _refresh_views(self) -> None:
        has_files = any(self._root.glob("camera_id=*/date=*/*.parquet"))
        if has_files:
            pattern = (self._root / "camera_id=*" / "date=*" / "*.parquet").as_posix()
            source = f"read_parquet('{_sql_string(pattern)}', hive_partitioning = false)"
        else:
            source = _EMPTY_TABLE
        self._con.execute(
            f"CREATE OR REPLACE VIEW {TRACK_POINTS_VIEW} AS "  # noqa: S608 - constant names
            f"SELECT {', '.join(COLUMNS)} FROM {source}"
        )
        self._con.execute(
            f"CREATE OR REPLACE VIEW {TRACKS_VIEW} AS "  # noqa: S608 - constant names
            f"SELECT DISTINCT track_id, camera_id, concept FROM {TRACK_POINTS_VIEW}"
        )


def _sql_string(value: str) -> str:
    return value.replace("'", "''")
