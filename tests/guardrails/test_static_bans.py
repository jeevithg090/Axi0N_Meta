"""Static guardrails (rule R13): banned capabilities must never appear in the source tree.

Anyone may add patterns here; nobody may weaken them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "rulecam"

BANNED_IMPORTS = re.compile(
    r"^\s*(?:import|from)\s+(face_recognition|deepface|insightface|arcface|facenet_pytorch)\b",
    re.MULTILINE,
)
BANNED_GROUPINGS = re.compile(
    r"group\s+by\s+[\w.]*track_id|groupby\(\s*['\"]track_id['\"]", re.IGNORECASE
)
BANNED_DLIB_FACE = re.compile(r"dlib\.(get_frontal_face_detector|face_recognition_model)")


def scan(text: str) -> list[str]:
    """Return the names of the guardrails `text` violates."""
    hits = []
    if BANNED_IMPORTS.search(text):
        hits.append("face-recognition import")
    if BANNED_GROUPINGS.search(text):
        hits.append("track_id grouping")
    if BANNED_DLIB_FACE.search(text):
        hits.append("dlib face model")
    return hits


def python_sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_source_tree_exists() -> None:
    assert python_sources(), "expected Python sources under src/rulecam"


@pytest.mark.parametrize("path", python_sources(), ids=lambda p: str(p.relative_to(SRC)))
def test_no_banned_capabilities(path: Path) -> None:
    assert scan(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("import face_recognition", "face-recognition import"),
        ("from deepface import DeepFace", "face-recognition import"),
        ("SELECT AVG(x) FROM t GROUP BY track_id", "track_id grouping"),
        ("df.groupby('track_id').mean()", "track_id grouping"),
        ("det = dlib.get_frontal_face_detector()", "dlib face model"),
    ],
)
def test_scanner_catches_banned_patterns(snippet: str, expected: str) -> None:
    assert expected in scan(snippet)


def test_scanner_allows_station_level_grouping() -> None:
    assert scan("SELECT station, AVG(wait) FROM dock_events GROUP BY station") == []
