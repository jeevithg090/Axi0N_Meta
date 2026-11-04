"""A read-only, incremental view of the evidence chain, indexed by event.

The feed shows each alert with the rule, measurement, strip and verifier verdict the runtime and
the crowd warden chained for its event. Re-reading a day's chain on every phone refresh would
grow with the day, so `ChainView` keeps a byte offset per day file and parses only the lines
appended since its last read. It never writes: staff actions are appended through
`EventChain.append` elsewhere.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from rulecam.log import get_logger

_log = get_logger(__name__)

DAYS_BACK = 1  # today and yesterday (UTC), so an alert from before midnight keeps its context


@dataclass
class EventRecords:
    """Every chained record of one event, in chain order.

    Attributes:
        candidate: The candidate payload's `candidate` object.
        context: The candidate payload's `context` (rule, calibration), when chained.
        assessment: A crowd warning's Spark assessment, when chained.
        candidate_ts: When the candidate was chained.
        verdicts: Verdict payloads (Tier 1, decisions, crowd checks).
        actions: Action payloads.
        acks: Ack payloads.
        labels: Label payloads.
        others: Any other record about the event (disputes, a retail fix, an arrival), with
            its kind as `_kind`.

    Every payload but the candidate's gets the record's `ts` and `hash` as `_ts` and `_hash`.
    """

    candidate: dict[str, object] | None = None
    context: dict[str, object] | None = None
    assessment: dict[str, object] | None = None
    candidate_ts: datetime | None = None
    verdicts: list[dict[str, object]] = field(default_factory=list)
    actions: list[dict[str, object]] = field(default_factory=list)
    acks: list[dict[str, object]] = field(default_factory=list)
    labels: list[dict[str, object]] = field(default_factory=list)
    others: list[dict[str, object]] = field(default_factory=list)


def _dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def event_id_of(payload: Mapping[str, object]) -> str | None:
    """The event a record's payload belongs to.

    A candidate's `event_id`, else the payload's `event_id`, else its `finding_id` (retail
    findings are alerted under their finding id).
    """
    candidate = _dict(payload.get("candidate"))
    found = candidate.get("event_id", payload.get("event_id", payload.get("finding_id")))
    return found if isinstance(found, str) else None


def _ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


class ChainView:
    """Indexes the site's chain files by event id, reading only what was appended."""

    def __init__(self, folder: Path, *, today: Callable[[], date]) -> None:
        """Create the view.

        Args:
            folder: The site's chain folder (`<root>/<site>`), one `<YYYY-MM-DD>.jsonl` per day.
            today: Today's UTC date (the clock's).
        """
        self._folder = folder
        self._today = today
        self._offsets: dict[Path, int] = {}
        self._events: dict[str, EventRecords] = {}
        self._lock = threading.Lock()

    def refresh(self) -> None:
        """Read the lines appended to today's and yesterday's chains since the last call."""
        today = self._today()
        days = [today - timedelta(days=k) for k in range(DAYS_BACK, -1, -1)]
        with self._lock:
            for day in days:
                self._read(self._folder / f"{day.isoformat()}.jsonl")

    def get(self, event_id: str) -> EventRecords | None:
        """The records of one event (call `refresh` first for the newest)."""
        with self._lock:
            return self._events.get(event_id)

    def event_ids(self) -> list[str]:
        """Every event with a chained candidate, oldest first."""
        with self._lock:
            return [k for k, v in self._events.items() if v.candidate is not None]

    def _read(self, path: Path) -> None:
        start = self._offsets.get(path, 0)
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                data = handle.read()
        except OSError:
            return
        end = data.rfind(b"\n")
        if end < 0:
            return
        self._offsets[path] = start + end + 1
        self._index(data[: end + 1].splitlines())

    def _index(self, lines: Iterable[bytes]) -> None:
        for raw in lines:
            try:
                record = json.loads(raw)
            except ValueError:
                _log.warning("staff_chain_line_unreadable")
                continue
            if isinstance(record, dict):
                self._add(record)

    def _add(self, record: Mapping[str, object]) -> None:
        payload = _dict(record.get("payload"))
        event_id = event_id_of(payload)
        if event_id is None:
            return
        entry = self._events.setdefault(event_id, EventRecords())
        kind = record.get("kind")
        if kind == "candidate":
            entry.candidate = _dict(payload.get("candidate"))
            entry.context = _dict(payload.get("context")) or None
            entry.assessment = _dict(payload.get("assessment")) or None
            entry.candidate_ts = _ts(record.get("ts"))
            return
        stamped = {**payload, "_ts": record.get("ts"), "_hash": record.get("hash")}
        if kind == "verdict":
            entry.verdicts.append(stamped)
        elif kind == "action":
            entry.actions.append(stamped)
        elif kind == "ack":
            entry.acks.append(stamped)
        elif kind == "label":
            entry.labels.append(stamped)
        else:
            entry.others.append({**stamped, "_kind": kind})
