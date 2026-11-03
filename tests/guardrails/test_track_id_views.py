"""Guardrail (rule R13): `track_id` never reaches an advisor-facing view (P1-04, with P1-12).

Only the perception-internal views of the track store (`PERCEPTION_INTERNAL_VIEWS`) may carry
`track_id`, and no view anywhere may group or partition by it. Anyone may add checks here; nobody
may weaken them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from rulecam.rules.store import PERCEPTION_INTERNAL_VIEWS, ParquetTrackStore
from tests.fakes import make_track

SRC = Path(__file__).resolve().parents[2] / "src" / "rulecam"
# Exempt on the team lead's decision (1 Nov 2026): owner analytics in prototype mode. `POST /ask`
# lets a store owner ask about dwell time, which needs one row per track (`track_spans`); the
# route is served only when RULECAM_PROTOTYPE_MODE is on. Nothing else is exempt.
PROTOTYPE_EXEMPT = frozenset({"ask/data.py"})

_VIEW = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:\{(?P<const>\w+)\}|(?P<name>\w+))\s+AS\b(?P<body>.*?)(?:;|\"\"\"|'''|\)\s*$)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
_GROUPS_BY_TRACK_ID = re.compile(
    r"(?:group|partition)\s+by\s+(?:[\w.\"]+\s*,\s*)*[\w.\"]*track_id\b", re.IGNORECASE
)
_CONSTANT_VIEW_NAMES = {"TRACK_POINTS_VIEW": "track_points", "TRACKS_VIEW": "tracks"}


def view_statements(text: str) -> list[tuple[str, str]]:
    """Return `(view_name, body)` for every `CREATE VIEW` statement found in source text."""
    out = []
    for found in _VIEW.finditer(text):
        name = found.group("name") or _CONSTANT_VIEW_NAMES.get(found.group("const"), "?")
        out.append((name.lower(), found.group("body")))
    return out


def violations(text: str, *, advisor_facing: bool) -> list[str]:
    """Return guardrail violations in `text`."""
    hits = []
    if _GROUPS_BY_TRACK_ID.search(text):
        hits.append("groups or partitions by track_id")
    for name, body in view_statements(text):
        exposed = advisor_facing or name not in PERCEPTION_INTERNAL_VIEWS
        if exposed and "track_id" in body.lower():
            hits.append(f"view {name} exposes track_id")
    return hits


def sources() -> list[Path]:
    return sorted(
        p for p in SRC.rglob("*.py") if p.relative_to(SRC).as_posix() not in PROTOTYPE_EXEMPT
    )


@pytest.mark.parametrize("path", sources(), ids=lambda p: str(p.relative_to(SRC)))
def test_no_view_exposes_or_groups_by_track_id(path: Path) -> None:
    advisor_facing = "advisor" in path.relative_to(SRC).parts
    assert violations(path.read_text(encoding="utf-8"), advisor_facing=advisor_facing) == []


def test_the_track_store_only_has_internal_views(tmp_path: Path) -> None:
    with ParquetTrackStore(tmp_path) as store:
        store.append([make_track(feet_m=[(0, 0), (1, 0)])])
        rows = store.connection.sql(
            "SELECT view_name, sql FROM duckdb_views() WHERE NOT internal AND NOT temporary"
        ).fetchall()
        tracks_columns = store.connection.sql("SELECT * FROM tracks").columns

    assert {name for name, _ in rows} == PERCEPTION_INTERNAL_VIEWS
    assert all(not _GROUPS_BY_TRACK_ID.search(sql) for _, sql in rows)
    assert set(tracks_columns) == {"track_id", "camera_id", "concept"}  # a catalogue, no metrics


def test_store_sql_is_recognised_by_the_scanner() -> None:
    store_source = (SRC / "rules" / "store.py").read_text(encoding="utf-8")

    assert {name for name, _ in view_statements(store_source)} == PERCEPTION_INTERNAL_VIEWS


@pytest.mark.parametrize(
    ("snippet", "advisor_facing", "expected"),
    [
        ("SELECT station, count(*) FROM t GROUP BY station, track_id", False, "groups"),
        ("SELECT avg(x) OVER (PARTITION BY track_id) FROM t", False, "groups"),
        ('"CREATE VIEW dock_waits AS SELECT track_id, wait FROM t;"', False, "exposes"),
        ('"CREATE OR REPLACE VIEW tracks AS SELECT track_id FROM t;"', True, "exposes"),
    ],
)
def test_scanner_catches_track_id_leaks(snippet: str, advisor_facing: bool, expected: str) -> None:
    assert any(expected in hit for hit in violations(snippet, advisor_facing=advisor_facing))


def test_scanner_allows_station_level_views() -> None:
    snippet = '"CREATE VIEW dock_waits AS SELECT station, avg(wait) FROM t GROUP BY station;"'

    assert violations(snippet, advisor_facing=True) == []
