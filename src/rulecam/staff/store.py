"""The staff app's own state: taps, first sightings and shelf tasks, in one JSON-lines file.

The evidence chain is the record of truth for every tap (each is appended there too); this file
lets the feed rebuild card states and response-time stats after a restart without re-parsing
every day's chain. Rows carry roles, never people.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from rulecam.contracts import Frozen
from rulecam.log import get_logger
from rulecam.staff.models import TakenAction

_log = get_logger(__name__)


class Sighting(Frozen):
    """The first time a role's phone showed an item.

    Attributes:
        item_id: The item.
        role: The phone's role.
        at: Server time the phone reported it.
        client_ms: The phone's own measure from the alert's timestamp to showing it.
    """

    item_id: str
    role: str
    at: datetime
    client_ms: float | None = None


class ShelfTask(Frozen):
    """A restock task made from a shelf gap that Muse Spark found and SAM 3.1 confirmed.

    Attributes:
        task_id: The task (also its event id in the chain).
        role: Who it is for.
        ts: When it was made.
        section: Bay and shelf, from Spark.
        neighbours: The products next to the gap, read from the packs by Spark.
        facings: About how many facings are missing.
        product_cover: Share of the gap SAM found product in (low means really empty).
        image: The marked shelf image (the gap numbered on it).
        camera_id: The camera, or `upload` for a phone photo.
        model: The model that found the gap.
        sample: Seeded sample data.
    """

    task_id: str
    role: str
    ts: datetime
    section: str
    neighbours: str
    facings: int
    product_cover: float
    image: str | None = None
    camera_id: str = "upload"
    model: str = ""
    sample: bool = False


class _ActionRow(Frozen):
    type: Literal["action"] = "action"
    item_id: str
    action: TakenAction
    photo: str | None = None


class _SeenRow(Frozen):
    type: Literal["seen"] = "seen"
    sighting: Sighting


class _TaskRow(Frozen):
    type: Literal["task"] = "task"
    task: ShelfTask


class _Row(Frozen):
    row: _ActionRow | _SeenRow | _TaskRow = Field(discriminator="type")


class StaffStore:
    """Thread-safe, append-only state of the staff app."""

    def __init__(self, path: Path) -> None:
        """Open (and replay) the state file.

        Args:
            path: The JSON-lines file, created on first write.
        """
        self._path = path
        self._lock = threading.Lock()
        self._actions: dict[str, list[TakenAction]] = {}
        self._photos: dict[str, list[str]] = {}
        self._seen: dict[tuple[str, str], Sighting] = {}
        self._tasks: dict[str, ShelfTask] = {}
        self._load()

    @property
    def path(self) -> Path:
        """The state file."""
        return self._path

    def record_action(self, item_id: str, action: TakenAction, photo: str | None = None) -> None:
        """Remember one tap (and the photo that came with a *Done*)."""
        row = _ActionRow(item_id=item_id, action=action, photo=photo)
        with self._lock:
            self._apply(row)
            self._write(row)

    def actions(self, item_id: str) -> list[TakenAction]:
        """Taps on an item, oldest first."""
        with self._lock:
            return list(self._actions.get(item_id, ()))

    def photos(self, item_id: str) -> list[str]:
        """Photos staff attached to an item, oldest first."""
        with self._lock:
            return list(self._photos.get(item_id, ()))

    def mark_seen(self, sighting: Sighting) -> bool:
        """Remember the first time a role's phone showed an item.

        Returns:
            True if this was the first sighting for that role.
        """
        key = (sighting.item_id, sighting.role)
        row = _SeenRow(sighting=sighting)
        with self._lock:
            if key in self._seen:
                return False
            self._apply(row)
            self._write(row)
        return True

    def sightings(self) -> list[Sighting]:
        """Every first sighting."""
        with self._lock:
            return list(self._seen.values())

    def add_task(self, task: ShelfTask) -> None:
        """Remember a new shelf task."""
        row = _TaskRow(task=task)
        with self._lock:
            self._apply(row)
            self._write(row)

    def tasks(self) -> list[ShelfTask]:
        """Every shelf task, oldest first."""
        with self._lock:
            return list(self._tasks.values())

    def _apply(self, row: _ActionRow | _SeenRow | _TaskRow) -> None:
        if isinstance(row, _ActionRow):
            self._actions.setdefault(row.item_id, []).append(row.action)
            if row.photo:
                self._photos.setdefault(row.item_id, []).append(row.photo)
        elif isinstance(row, _SeenRow):
            self._seen[(row.sighting.item_id, row.sighting.role)] = row.sighting
        else:
            self._tasks[row.task.task_id] = row.task

    def _write(self, row: _ActionRow | _SeenRow | _TaskRow) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as out:
            out.write(row.model_dump_json() + "\n")

    def _load(self) -> None:
        if not self._path.is_file():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = _Row.model_validate({"row": json.loads(line)})
            except (ValueError, ValidationError):
                _log.warning("staff_state_line_skipped")
                continue
            self._apply(parsed.row)
