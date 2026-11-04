"""Where the supermarket's findings, watches and restock tasks are kept (`<data>/retail/`).

- `findings.jsonl`: one line per check, appended (survives restarts);
- `watches.json`: the standing watches the owner turned on;
- `tasks.json`: restock tasks and their status.

Each store is thread-safe: the watch ticker and the API threads use them at once.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ValidationError

from rulecam.log import get_logger
from rulecam.retail.models import Finding, StaffTask, TaskStatus, Watch

_log = get_logger(__name__)

MAX_FINDINGS: Final = 2000


def _read_lines[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    if not path.is_file():
        return []
    out: list[M] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(model.model_validate_json(line))
        except ValidationError:
            _log.warning("retail_bad_line", path=path.name)
    return out


class FindingLog:
    """The check results, newest last, in memory and appended to a JSON-lines file."""

    def __init__(self, path: Path) -> None:
        """Load the findings already in `path`."""
        self._path = path
        self._lock = threading.Lock()
        self._items = _read_lines(path, Finding)[-MAX_FINDINGS:]

    def add(self, finding: Finding) -> Finding:
        """Keep one finding."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as out:
                out.write(finding.model_dump_json() + "\n")
            self._items.append(finding)
            del self._items[:-MAX_FINDINGS]
        return finding

    def get(self, finding_id: str) -> Finding | None:
        """One finding by id."""
        with self._lock:
            return next((f for f in reversed(self._items) if f.id == finding_id), None)

    def recent(
        self,
        *,
        limit: int = 50,
        kind: str | None = None,
        watch_id: str | None = None,
        since: datetime | None = None,
    ) -> list[Finding]:
        """Newest first, optionally of one kind or watch, or since an instant."""
        with self._lock:
            items = list(reversed(self._items))
        seen: set[str] = set()
        out: list[Finding] = []
        for f in items:
            if f.id in seen:
                continue
            seen.add(f.id)
            if kind is not None and f.kind != kind:
                continue
            if watch_id is not None and f.watch_id != watch_id:
                continue
            if since is not None and f.checked_at < since:
                continue
            out.append(f)
            if len(out) >= limit:
                break
        return out


class _JsonBook[T: BaseModel]:
    """A small id -> record map saved as one JSON file."""

    def __init__(self, path: Path, model: type[T], key: Callable[[T], str]) -> None:
        self._path = path
        self._model = model
        self._key = key
        self._lock = threading.Lock()
        self._items: dict[str, T] = {}
        if path.is_file():
            try:
                for raw in json.loads(path.read_text(encoding="utf-8")):
                    item = model.model_validate(raw)
                    self._items[key(item)] = item
            except (ValueError, ValidationError):
                _log.warning("retail_bad_book", path=path.name)

    def put(self, item: T) -> T:
        with self._lock:
            self._items[self._key(item)] = item
            self._save()
        return item

    def get(self, item_id: str) -> T | None:
        with self._lock:
            return self._items.get(item_id)

    def all(self) -> list[T]:
        with self._lock:
            return list(self._items.values())

    def remove(self, item_id: str) -> T | None:
        with self._lock:
            item = self._items.pop(item_id, None)
            self._save()
        return item

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([i.model_dump(mode="json") for i in self._items.values()], indent=1),
            encoding="utf-8",
        )
        tmp.replace(self._path)


class WatchBook(_JsonBook[Watch]):
    """The standing watches."""

    def __init__(self, path: Path) -> None:
        """Load `path` (a missing file is an empty book)."""
        super().__init__(path, Watch, lambda w: w.id)


class TaskBoard(_JsonBook[StaffTask]):
    """Staff tasks; every change is also appended to `<name>.events.jsonl` (Ask reads it)."""

    def __init__(self, path: Path) -> None:
        """Load `path` (a missing file is an empty board)."""
        super().__init__(path, StaffTask, lambda t: t.id)
        self.events = path.with_suffix(".events.jsonl")

    def put(self, item: StaffTask) -> StaffTask:
        """Store a task and log the change."""
        stored = super().put(item)
        with self._lock, self.events.open("a", encoding="utf-8") as out:
            out.write(stored.model_dump_json() + "\n")
        return stored

    def set_status(self, task_id: str, status: TaskStatus, at: datetime) -> StaffTask | None:
        """Move a task on (`done` stamps `done_at`)."""
        task = self.get(task_id)
        if task is None:
            return None
        update: dict[str, object] = {"status": status}
        if status == "done":
            update.update(done_at=at, closed_by="staff", minutes_to_fix=minutes(task, at))
        return self.put(task.model_copy(update=update))


def minutes(task: StaffTask, done: datetime) -> float:
    """Minutes from when the problem was seen to `done`."""
    seen = task.seen_at or task.created_at
    return round(max(0.0, (done - seen).total_seconds()) / 60, 1)
