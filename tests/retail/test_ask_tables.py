"""Ask can query the supermarket's findings and tasks (`retail_findings`, `retail_tasks`)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from rulecam.ask.data import QueryResult, TrackTables
from rulecam.retail.models import Finding, StaffTask
from rulecam.retail.store import FindingLog, TaskBoard
from tests.retail.conftest import AT


def test_retail_tables(tmp_path: Path) -> None:
    folder = tmp_path / "retail"
    log = FindingLog(folder / "findings.jsonl")
    log.add(
        Finding(
            id="f-1",
            kind="shelf",
            camera_id="aisle",
            at=AT,
            checked_at=AT,
            act=True,
            headline="2 shelf gaps",
            metric=2.0,
            detail={"gaps": [{"number": 1}]},
        )
    )
    log.add(
        Finding(
            id="f-2",
            kind="queue",
            camera_id="till",
            at=None,
            checked_at=AT,
            act=False,
            headline="q",
        )
    )
    board = TaskBoard(folder / "tasks.json")
    task = board.put(
        StaffTask(
            id="t-1",
            finding_id="f-1",
            camera_id="aisle",
            where="aisle 3",
            items=["gap 1"],
            marked=None,
            seen_at=AT,
            created_at=AT,
        )
    )
    board.set_status(task.id, "done", AT + timedelta(minutes=7))
    tables = TrackTables(tmp_path / "tracks")
    findings = tables.query("SELECT id, kind, seen_at, metric FROM retail_findings ORDER BY id")
    assert isinstance(findings, QueryResult)
    assert findings.rows == [
        ["f-1", "shelf", "2026-11-04T09:00:00Z", 2.0],
        ["f-2", "queue", None, None],
    ]
    tasks = tables.query("SELECT id, place, status, minutes_to_fix, closed_by FROM retail_tasks")
    assert isinstance(tasks, QueryResult)
    assert tasks.rows == [["t-1", "aisle 3", "done", 7.0, "staff"]]


def test_retail_tables_empty(tmp_path: Path) -> None:
    tables = TrackTables(tmp_path / "tracks")
    for sql in ("SELECT count(*) FROM retail_findings", "SELECT count(*) FROM retail_tasks"):
        result = tables.query(sql)
        assert isinstance(result, QueryResult)
        assert result.rows == [[0]]
