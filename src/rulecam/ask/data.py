"""What the cameras recorded, as tables the model can query with SQL (`POST /ask`).

Each question gets a fresh in-memory DuckDB snapshot of the track store's Parquet files (times
are `TIMESTAMP` in UTC, without a zone):

- `track_points`: one row per tracked object per keyframe (the runtime's SAM 3.1 keyframes,
  joined into tracks by ByteTrack): `track_id, camera_id, concept, ts, frame_idx, x1, y1, x2,
  y2, foot_px_x, foot_px_y, foot_m_x, foot_m_y, conf`;
- `track_spans`: one row per track: `track_id, camera_id, concept, first_seen, last_seen,
  seconds, points`;
- `captions`: what Muse Glimmer saw on each camera every few seconds (`ask.memory`):
  `camera_id, t0, t1, caption, people, events`.

After the snapshot the connection loses file access (`enable_external_access = false`, locked),
so a query can read these tables and nothing else. Only one `SELECT` or `WITH` statement runs;
results are capped at `MAX_ROWS` rows.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import duckdb

from rulecam.contracts import Frozen

MAX_ROWS: Final = 60
_COLUMNS: Final = (
    "track_id, camera_id, concept, CAST(ts AS TIMESTAMP) AS ts, frame_idx, x1, y1, x2, y2, "
    "foot_px_x, foot_px_y, foot_m_x, foot_m_y, conf"
)
_READ_ONLY: Final = re.compile(r"^\s*(?:select|with)\b", re.IGNORECASE)


class SqlError(ValueError):
    """A query that is not a single read-only statement, or that DuckDB rejected."""


class QueryResult(Frozen):
    """A query's answer, capped at `MAX_ROWS` rows.

    Attributes:
        columns: Column names.
        rows: Rows as lists of JSON-friendly values (timestamps as ISO text).
        truncated: Whether more rows existed.
    """

    columns: list[str]
    rows: list[list[object]]
    truncated: bool = False


class CameraData(Frozen):
    """What one camera recorded: its time range and the tracked concepts with counts.

    Attributes:
        camera_id: The camera.
        first_seen: First tracked point (UTC).
        last_seen: Last tracked point (UTC).
        concepts: Concept -> number of tracks.
    """

    camera_id: str
    first_seen: datetime
    last_seen: datetime
    concepts: dict[str, int]


def _cell(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    if isinstance(value, float):
        return round(value, 4)
    return value


class TrackTables:
    """A per-question snapshot of the track store in DuckDB."""

    def __init__(self, root: Path, captions: Path | None = None) -> None:
        """Snapshot every Parquet partition under `root` (and the captions) and lock file access.

        Args:
            root: The track store's directory (`<data>/tracks`).
            captions: The caption memory's JSON-lines file, if any.
        """
        self._con = duckdb.connect()
        self._con.execute("SET TimeZone = 'UTC'")
        files = sorted(root.glob("camera_id=*/date=*/*.parquet"))
        if files:
            pattern = (root / "camera_id=*" / "date=*" / "*.parquet").as_posix().replace("'", "''")
            self._con.execute(
                f"CREATE TABLE track_points AS SELECT {_COLUMNS} "  # noqa: S608 - constant parts
                f"FROM read_parquet('{pattern}', hive_partitioning = false, union_by_name = true)"
            )
        else:
            self._con.execute(
                "CREATE TABLE track_points (track_id VARCHAR, camera_id VARCHAR, concept VARCHAR, "
                "ts TIMESTAMP, frame_idx BIGINT, x1 DOUBLE, y1 DOUBLE, x2 DOUBLE, y2 DOUBLE, "
                "foot_px_x DOUBLE, foot_px_y DOUBLE, foot_m_x DOUBLE, foot_m_y DOUBLE, conf DOUBLE)"
            )
        self._con.execute(
            "CREATE TABLE track_spans AS SELECT track_id, camera_id, concept, "
            "min(ts) AS first_seen, max(ts) AS last_seen, "
            "epoch(max(ts)) - epoch(min(ts)) AS seconds, count(*) AS points "
            "FROM track_points GROUP BY track_id, camera_id, concept"
        )
        if captions is not None and captions.is_file() and captions.stat().st_size:
            source = captions.as_posix().replace("'", "''")
            self._con.execute(
                "CREATE TABLE captions AS SELECT camera_id, CAST(t0 AS TIMESTAMP) AS t0, "  # noqa: S608
                "CAST(t1 AS TIMESTAMP) AS t1, caption, people, events "
                f"FROM read_json_auto('{source}', format = 'newline_delimited')"
            )
        else:
            self._con.execute(
                "CREATE TABLE captions (camera_id VARCHAR, t0 TIMESTAMP, t1 TIMESTAMP, "
                "caption VARCHAR, people BIGINT, events VARCHAR[])"
            )
        self._con.execute("SET enable_external_access = false")
        self._con.execute("SET lock_configuration = true")

    def close(self) -> None:
        """Close the snapshot."""
        self._con.close()

    def cameras(self) -> list[CameraData]:
        """What each camera recorded, cameras in name order."""
        rows = self._con.execute(
            "SELECT camera_id, min(first_seen), max(last_seen), concept, count(*) "
            "FROM track_spans GROUP BY camera_id, concept ORDER BY camera_id, concept"
        ).fetchall()
        found: dict[str, CameraData] = {}
        for camera_id, first_naive, last_naive, concept, tracks in rows:
            first, last = first_naive.replace(tzinfo=UTC), last_naive.replace(tzinfo=UTC)
            seen = found.get(camera_id)
            if seen is None:
                found[camera_id] = CameraData(
                    camera_id=camera_id, first_seen=first, last_seen=last, concepts={}
                )
                seen = found[camera_id]
            found[camera_id] = seen.model_copy(
                update={
                    "first_seen": min(seen.first_seen, first),
                    "last_seen": max(seen.last_seen, last),
                    "concepts": {**seen.concepts, concept: int(tracks)},
                }
            )
        return list(found.values())

    def stats(self) -> list[dict[str, object]]:
        """Per camera and concept: tracks, most at once, median track length, keyframes/s."""
        result = self.query(
            "WITH per_ts AS (SELECT camera_id, concept, ts, count(*) AS n FROM track_points "
            "GROUP BY camera_id, concept, ts), rate AS (SELECT camera_id, "
            "count(DISTINCT ts) / greatest(epoch(max(ts)) - epoch(min(ts)), 1) AS hz "
            "FROM track_points GROUP BY camera_id) "
            "SELECT s.camera_id, s.concept, count(*) AS tracks, "
            "(SELECT max(n) FROM per_ts p WHERE p.camera_id = s.camera_id "
            "AND p.concept = s.concept) AS most_at_once, "
            "round(median(s.seconds), 1) AS median_track_s, "
            "round(any_value(r.hz), 2) AS keyframes_per_s "
            "FROM track_spans s JOIN rate r USING (camera_id) "
            "GROUP BY s.camera_id, s.concept ORDER BY s.camera_id, s.concept"
        )
        return [dict(zip(result.columns, row, strict=True)) for row in result.rows]

    def recent_captions(self) -> list[dict[str, object]]:
        """The 4 newest captions of each camera, oldest first."""
        result = self.query(
            "SELECT camera_id, t0, t1, caption, people FROM (SELECT *, row_number() OVER "
            "(PARTITION BY camera_id ORDER BY t1 DESC) AS k FROM captions) "
            "WHERE k <= 4 ORDER BY camera_id, t1"
        )
        return [dict(zip(result.columns, row, strict=True)) for row in result.rows]

    def boxes_near(
        self, camera_id: str, at: datetime, within_s: float = 0.6
    ) -> list[tuple[str, str, tuple[float, float, float, float]]]:
        """Each track's box nearest to `at` on `camera_id`, within `within_s` seconds.

        Returns:
            `(track_id, concept, (x1, y1, x2, y2))`, in track order.
        """
        naive = at.astimezone(UTC).replace(tzinfo=None)
        rows = self._con.execute(
            "SELECT track_id, concept, x1, y1, x2, y2 FROM track_points WHERE camera_id = ? "
            "AND abs(epoch(ts) - epoch(CAST(? AS TIMESTAMP))) <= ? "
            "QUALIFY row_number() OVER (PARTITION BY track_id "
            "ORDER BY abs(epoch(ts) - epoch(CAST(? AS TIMESTAMP)))) = 1 ORDER BY track_id",
            [camera_id, naive, within_s, naive],
        ).fetchall()
        return [(t, c, (x1, y1, x2, y2)) for t, c, x1, y1, x2, y2 in rows]

    def query(self, sql: str) -> QueryResult:
        """Run one read-only query.

        Args:
            sql: A single `SELECT` or `WITH` statement (a trailing `;` is allowed).

        Returns:
            The result, at most `MAX_ROWS` rows.

        Raises:
            SqlError: For anything but one read-only statement, or a DuckDB error.
        """
        text = sql.strip().rstrip(";").strip()
        if not _READ_ONLY.match(text) or ";" in text:
            msg = "only one SELECT or WITH statement is allowed"
            raise SqlError(msg)
        try:
            cursor = self._con.execute(text)
            rows = cursor.fetchmany(MAX_ROWS + 1)
        except duckdb.Error as exc:
            raise SqlError(str(exc).splitlines()[0]) from exc
        columns = [d[0] for d in cursor.description or []]
        return QueryResult(
            columns=columns,
            rows=[[_cell(v) for v in row] for row in rows[:MAX_ROWS]],
            truncated=len(rows) > MAX_ROWS,
        )
