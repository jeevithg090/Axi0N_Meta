"""What the cameras recorded, as tables the model can query with SQL (`POST /ask`).

Each question gets a fresh in-memory DuckDB snapshot of the track store's Parquet files (times
are `TIMESTAMP` in UTC, without a zone):

- `track_points`: one row per tracked object per keyframe (the runtime's SAM 3.1 keyframes,
  joined into tracks by ByteTrack): `track_id, camera_id, concept, ts, frame_idx, x1, y1, x2,
  y2, foot_px_x, foot_px_y, foot_m_x, foot_m_y, conf`;
- `track_spans`: one row per track: `track_id, camera_id, concept, first_seen, last_seen,
  seconds, points`;
- `captions`: what Muse Glimmer saw on each camera every few seconds (`ask.memory`):
  `camera_id, t0, t1, caption, people, events`;
- `track_identity`, `identity_spans`, `identity_notes`: track fragments joined into identities
  by appearance (`ask.identity`), each identity's span, and Glimmer's note on each person.
- `retail_findings`, `retail_tasks`: the supermarket checks (queue, hazard, shelf; what the
  models judged and whether staff were alerted) and the staff tasks they opened, with when each
  was seen and fixed (`rulecam.retail`, beside the track store in `<data>/retail/`);
- `crowd_readings`, `crowd_assessments`, `crowd_alerts`: crowd density, motion and pressure
  per zone every few seconds (`crowd.memory`), the warden's assessments and its alerts
  (`crowd.warden`); empty when crowd watch is off.

After the snapshot the connection loses file access (`enable_external_access = false`, locked),
so a query can read these tables and nothing else. Only `SELECT` or `WITH` statements run
(checked with DuckDB's own parser); several separated by `;` run one by one (`run_sql`).
Results are capped at `max_rows` rows (`RULECAM_ASK_MAX_ROWS`); a capped result says so and
carries the query's full row count, so the model never takes part of a result for all of it.
Every read runs on its own cursor, so the tool calls of one turn can query at the same time.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import duckdb

from rulecam.contracts import Frozen

DEFAULT_MAX_ROWS: Final = 1000
_COLUMNS: Final = (
    "track_id, camera_id, concept, CAST(ts AS TIMESTAMP) AS ts, frame_idx, x1, y1, x2, y2, "
    "foot_px_x, foot_px_y, foot_m_x, foot_m_y, conf"
)


_CROWD_READINGS: Final = (
    "camera_id VARCHAR, ts TIMESTAMP, zone VARCHAR, people BIGINT, area_m2 DOUBLE, "
    "density_ppm2 DOUBLE, moving BIGINT, speed_mps DOUBLE, speed_sd_mps DOUBLE, "
    "velocity_var DOUBLE, coherence DOUBLE, counterflow_share DOUBLE, pressure DOUBLE, "
    "peak_density_ppm2 DOUBLE, peak_row BIGINT, peak_col BIGINT, calibration VARCHAR, "
    "calibration_err_m DOUBLE, concept VARCHAR, heatmap VARCHAR"
)
_CROWD_ASSESSMENTS: Final = (
    "camera_id VARCHAR, ts TIMESTAMP, level VARCHAR, zones VARCHAR[], why VARCHAR, "
    "forecast VARCHAR, minutes_to_danger DOUBLE, alert BOOLEAN, staff_actions VARCHAR[]"
)
_RETAIL_FINDINGS: Final = {
    "id": "VARCHAR",
    "kind": "VARCHAR",
    "camera_id": "VARCHAR",
    "camera_name": "VARCHAR",
    "at": "VARCHAR",
    "checked_at": "VARCHAR",
    "watch_id": "VARCHAR",
    "act": "BOOLEAN",
    "headline": "VARCHAR",
    "staff_message": "VARCHAR",
    "metric": "DOUBLE",
    "alert_id": "VARCHAR",
    "task_id": "VARCHAR",
}
_RETAIL_TASKS: Final = {
    "id": "VARCHAR",
    "kind": "VARCHAR",
    "camera_id": "VARCHAR",
    "where": "VARCHAR",
    "items": "VARCHAR[]",
    "seen_at": "VARCHAR",
    "created_at": "VARCHAR",
    "status": "VARCHAR",
    "done_at": "VARCHAR",
    "closed_by": "VARCHAR",
    "minutes_to_fix": "DOUBLE",
    "role": "VARCHAR",
}
_CROWD_ALERTS: Final = (
    "alert_id VARCHAR, camera_id VARCHAR, ts TIMESTAMP, level VARCHAR, sent BOOLEAN, "
    "held_reason VARCHAR"
)


class CrowdFiles(Frozen):
    """The crowd memory's and warden's JSON-lines files (each optional)."""

    readings: Path | None = None
    assessments: Path | None = None
    alerts: Path | None = None


class SqlError(ValueError):
    """A query that is not a single read-only statement, or that DuckDB rejected."""


class QueryResult(Frozen):
    """A query's answer, capped at the snapshot's `max_rows` rows.

    Attributes:
        columns: Column names.
        rows: Rows as lists of JSON-friendly values (timestamps as ISO text).
        truncated: Whether more rows existed.
        total_rows: The query's full row count when it was capped (`None` when it was not, or
            when counting failed).
        note: What was left out, in words, when the result was capped.
    """

    columns: list[str]
    rows: list[list[object]]
    truncated: bool = False
    total_rows: int | None = None
    note: str | None = None


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


def capped_note(shown: int, total: int | None) -> str:
    """Say that a result shows only its first `shown` rows of `total` (unknown: `None`)."""
    of = f"{total}" if total is not None else "more"
    return (
        f"showing the first {shown} of {of} rows (at most {shown} rows per query); aggregate in "
        "SQL, or page with LIMIT/OFFSET, to see the rest"
    )


class TrackTables:
    """A per-question snapshot of the track store in DuckDB."""

    def __init__(
        self,
        root: Path,
        captions: Path | None = None,
        identities: Path | None = None,
        notes: Path | None = None,
        *,
        crowd: CrowdFiles | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> None:
        """Snapshot the track store and the memories, then lock file access.

        Args:
            root: The track store's directory (`<data>/tracks`).
            captions: The caption memory's JSON-lines file, if any.
            identities: The identity memory's track -> identity file, if any.
            notes: The identity notes file, if any.
            crowd: The crowd memory's and warden's files, if crowd watch is on.
            max_rows: Rows a query returns at most; the rest is counted and reported.
        """
        self._max_rows = max_rows
        self._con = duckdb.connect()
        # GLOBAL, so the per-call cursors read timestamps in UTC as well.
        self._con.execute("SET GLOBAL TimeZone = 'UTC'")
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
        self._json_table(
            "track_identity",
            identities,
            "track_id VARCHAR, camera_id VARCHAR, concept VARCHAR, identity_id VARCHAR, "
            "similarity DOUBLE, threshold DOUBLE",
        )
        self._json_table(
            "identity_notes",
            notes,
            "identity_id VARCHAR, role VARCHAR, description VARCHAR, carrying VARCHAR",
        )
        self._crowd_tables(crowd or CrowdFiles())
        self._retail_tables(root.parent / "retail")
        # Relinking can retire an identity id; its note then describes no current identity.
        self._con.execute(
            "DELETE FROM identity_notes WHERE identity_id NOT IN "
            "(SELECT identity_id FROM track_identity)"
        )
        self._con.execute(
            "CREATE TABLE identity_spans AS SELECT i.identity_id, i.camera_id, i.concept, "
            "min(s.first_seen) AS first_seen, max(s.last_seen) AS last_seen, "
            "epoch(max(s.last_seen)) - epoch(min(s.first_seen)) AS seconds, "
            "count(*) AS fragments, sum(s.seconds) AS seen_seconds "
            "FROM track_identity i JOIN track_spans s USING (track_id) "
            "GROUP BY i.identity_id, i.camera_id, i.concept"
        )
        self._con.execute("SET enable_external_access = false")
        self._con.execute("SET lock_configuration = true")

    def _crowd_tables(self, files: CrowdFiles) -> None:
        """`crowd_readings`, `crowd_assessments` and `crowd_alerts`, timestamps in UTC."""
        self._json_table("crowd_readings", files.readings, _CROWD_READINGS, timestamps=("ts",))
        self._json_table(
            "crowd_assessments", files.assessments, _CROWD_ASSESSMENTS, timestamps=("ts",)
        )
        self._json_table("crowd_alerts", files.alerts, _CROWD_ALERTS, timestamps=("ts",))

    def _retail_tables(self, folder: Path) -> None:
        """`retail_findings` (one row per check) and `retail_tasks` (each task's latest state)."""
        self._typed_table(
            "retail_findings",
            folder / "findings.jsonl",
            _RETAIL_FINDINGS,
            "id, kind, camera_id, camera_name, {at} AS seen_at, {checked_at} AS checked_at, "
            "watch_id, act, headline, staff_message, metric, alert_id, task_id",
            dedupe="checked_at",
        )
        self._typed_table(
            "retail_tasks",
            folder / "tasks.events.jsonl",
            _RETAIL_TASKS,
            'id, kind, camera_id, "where" AS place, items, {seen_at} AS seen_at, '
            "{created_at} AS created_at, status, {done_at} AS done_at, closed_by, "
            "minutes_to_fix, role",
            dedupe="row",
        )

    def _typed_table(
        self, name: str, path: Path, columns: dict[str, str], select: str, *, dedupe: str
    ) -> None:
        """Load chosen keys of a JSON-lines file; ISO text times become UTC `TIMESTAMP`s.

        `select` names time columns as `{col}`; with `dedupe="row"` the last line per `id`
        is kept (a log of states), else every line is one row.
        """
        times = {
            c: f'CAST(CAST("{c}" AS TIMESTAMPTZ) AS TIMESTAMP)'
            for c in columns
            if c.endswith(("at", "_at"))
        }
        projection = select.format(**times)
        spec = "{" + ", ".join(f"'{k}': '{v}'" for k, v in columns.items()) + "}"
        if path.is_file() and path.stat().st_size:
            source = path.as_posix().replace("'", "''")
            body = (
                f"SELECT {projection}, row_number() OVER () AS line_no FROM "  # noqa: S608
                f"read_json('{source}', format = 'newline_delimited', columns = {spec})"
            )
            if dedupe == "row":
                body = (
                    f"SELECT * EXCLUDE (line_no) FROM ({body}) "  # noqa: S608 - constant parts
                    "QUALIFY row_number() OVER (PARTITION BY id ORDER BY line_no DESC) = 1"
                )
            else:
                body = f"SELECT * EXCLUDE (line_no) FROM ({body})"  # noqa: S608
            self._con.execute(f"CREATE TABLE {name} AS {body}")
            return
        empty = (
            f"SELECT {projection} FROM (SELECT "  # noqa: S608 - constant parts
            + ", ".join(f'CAST(NULL AS {v}) AS "{k}"' for k, v in columns.items())
            + ") WHERE false"
        )
        self._con.execute(f"CREATE TABLE {name} AS {empty}")

    def _json_table(
        self, name: str, path: Path | None, columns: str, *, timestamps: Sequence[str] = ()
    ) -> None:
        """Load a JSON-lines file as table `name`, or create it empty with `columns`."""
        if path is not None and path.is_file() and path.stat().st_size:
            source = path.as_posix().replace("'", "''")
            cast = ", ".join(
                f"CAST(CAST({c} AS TIMESTAMPTZ) AS TIMESTAMP) AS {c}" for c in timestamps
            )
            star = f"* REPLACE ({cast})" if cast else "*"
            self._con.execute(
                f"CREATE TABLE {name} AS SELECT {star} FROM "  # noqa: S608 - constant name and path
                f"read_json_auto('{source}', format = 'newline_delimited')"
            )
        else:
            self._con.execute(f"CREATE TABLE {name} ({columns})")

    def _rows(self, sql: str, params: Sequence[object] = ()) -> list[tuple[object, ...]]:
        """Run a fixed internal query on its own cursor and return the raw rows."""
        with self._con.cursor() as cursor:
            return cursor.execute(sql, list(params)).fetchall()

    def query_params(self, sql: str, params: Sequence[object]) -> list[tuple[object, ...]]:
        """Run a fixed internal query with parameters and return the raw rows."""
        return self._rows(sql, params)

    def track_samples(
        self, track_id: str, n: int
    ) -> list[tuple[datetime, tuple[float, float, float, float]]]:
        """Up to `n` points of a track, spread evenly over it: `(UTC instant, box)`."""
        rows: list[tuple[datetime, float, float, float, float]] = self._rows(  # type: ignore[assignment]
            "SELECT ts, x1, y1, x2, y2 FROM track_points WHERE track_id = ? ORDER BY ts",
            [track_id],
        )
        if not rows:
            return []
        picks = sorted({round(k * (len(rows) - 1) / max(1, n - 1)) for k in range(n)})
        return [
            (rows[i][0].replace(tzinfo=UTC), (rows[i][1], rows[i][2], rows[i][3], rows[i][4]))
            for i in picks
        ]

    def close(self) -> None:
        """Close the snapshot."""
        self._con.close()

    def cameras(self) -> list[CameraData]:
        """What each camera recorded, cameras in name order."""
        rows: list[tuple[str, datetime, datetime, str, int]] = self._rows(  # type: ignore[assignment]
            "SELECT camera_id, min(first_seen), max(last_seen), concept, count(*) "
            "FROM track_spans GROUP BY camera_id, concept ORDER BY camera_id, concept"
        )
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
        """Per camera and concept: tracks, identities, most at once, median lengths, keyframes/s."""
        result = self.query(
            "WITH per_ts AS (SELECT camera_id, concept, ts, count(*) AS n FROM track_points "
            "GROUP BY camera_id, concept, ts), rate AS (SELECT camera_id, "
            "count(DISTINCT ts) / greatest(epoch(max(ts)) - epoch(min(ts)), 1) AS hz "
            "FROM track_points GROUP BY camera_id), ids AS (SELECT camera_id, concept, "
            "count(*) AS identities, round(median(seconds), 1) AS median_identity_s "
            "FROM identity_spans GROUP BY camera_id, concept) "
            "SELECT s.camera_id, s.concept, count(*) AS tracks, "
            "any_value(i.identities) AS identities, "
            "(SELECT max(n) FROM per_ts p WHERE p.camera_id = s.camera_id "
            "AND p.concept = s.concept) AS most_at_once, "
            "round(median(s.seconds), 1) AS median_track_s, "
            "any_value(i.median_identity_s) AS median_identity_s, "
            "round(any_value(r.hz), 2) AS keyframes_per_s "
            "FROM track_spans s JOIN rate r USING (camera_id) "
            "LEFT JOIN ids i USING (camera_id, concept) "
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
        rows: list[tuple[str, str, float, float, float, float]] = self._rows(  # type: ignore[assignment]
            "SELECT track_id, concept, x1, y1, x2, y2 FROM track_points WHERE camera_id = ? "
            "AND abs(epoch(ts) - epoch(CAST(? AS TIMESTAMP))) <= ? "
            "QUALIFY row_number() OVER (PARTITION BY track_id "
            "ORDER BY abs(epoch(ts) - epoch(CAST(? AS TIMESTAMP)))) = 1 ORDER BY track_id",
            [camera_id, naive, within_s, naive],
        )
        return [(t, c, (x1, y1, x2, y2)) for t, c, x1, y1, x2, y2 in rows]

    def run_sql(self, sql: str) -> list[tuple[str, QueryResult | SqlError]]:
        """Run one or more read-only statements (separated by `;`), each on its own.

        Args:
            sql: `SELECT` or `WITH` statements.

        Returns:
            Each statement with its result, or the error it alone hit.

        Raises:
            SqlError: When the text does not parse, or any statement is not read-only (then
                none of them runs).
        """
        out: list[tuple[str, QueryResult | SqlError]] = []
        for text in read_only_statements(sql):
            try:
                out.append((text, self._execute(text)))
            except SqlError as exc:
                out.append((text, exc))
        return out

    def query(self, sql: str) -> QueryResult:
        """Run one read-only query.

        Args:
            sql: A single `SELECT` or `WITH` statement (a trailing `;` is allowed).

        Returns:
            The result, at most `max_rows` rows; a capped one has `truncated`, the full row
            count in `total_rows` and a `note` saying how much it shows.

        Raises:
            SqlError: For anything but one read-only statement, or a DuckDB error.
        """
        statements = read_only_statements(sql)
        if len(statements) != 1:
            msg = "only one SELECT or WITH statement is allowed here"
            raise SqlError(msg)
        return self._execute(statements[0])

    def _execute(self, text: str) -> QueryResult:
        """Run one statement already checked to be read-only, capped at `max_rows` rows."""
        with self._con.cursor() as cursor:
            try:
                cursor.execute(text)
                rows = cursor.fetchmany(self._max_rows + 1)
            except duckdb.Error as exc:
                raise SqlError(str(exc).splitlines()[0]) from exc
            columns = [d[0] for d in cursor.description or []]
            total = _count(cursor, text) if len(rows) > self._max_rows else None
        shown = [[_cell(v) for v in row] for row in rows[: self._max_rows]]
        if len(rows) <= self._max_rows:
            return QueryResult(columns=columns, rows=shown)
        return QueryResult(
            columns=columns,
            rows=shown,
            truncated=True,
            total_rows=total,
            note=capped_note(self._max_rows, total),
        )


def read_only_statements(sql: str) -> list[str]:
    """Split SQL into its statements (DuckDB's own parser) and check each one only reads.

    Args:
        sql: One or more statements separated by `;`.

    Returns:
        Each statement's text, without its `;`.

    Raises:
        SqlError: When the text does not parse, holds no statement, or any statement is not a
            `SELECT` (which includes `WITH ...` and `FROM ...` queries).
    """
    try:
        found = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise SqlError(str(exc).splitlines()[0]) from exc
    if not found:
        msg = "no SQL statement given"
        raise SqlError(msg)
    for statement in found:
        if statement.type != duckdb.StatementType.SELECT:
            msg = f"only SELECT or WITH statements are allowed, not {statement.type.name}"
            raise SqlError(msg)
    return [s.query.strip().rstrip(";").strip() for s in found]


def _count(cursor: duckdb.DuckDBPyConnection, text: str) -> int | None:
    """The full row count of a read-only query, or `None` when counting it fails."""
    try:
        found = cursor.execute(f"SELECT count(*) FROM ({text})").fetchone()  # noqa: S608
    except duckdb.Error:
        return None
    return int(found[0]) if found else None
