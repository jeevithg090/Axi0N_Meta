"""The files of one recorded-run golden fixture, and reading them back.

```
scenarios/<name>/
  run.json              the eval harness's RunRecord of the live record run
  plan.json             the approved SitePlan (GET /plans/{id})
  response.json         how the runtime's policy was configured (`/runtime/metrics` `response`)
  chain.jsonl           the day's evidence chain, from its first record
  windows.json          the track window the rules engine read for each candidate
  strips/<event>/<k>.jpg  the Tier 1 strips, byte for byte as Glimmer saw them
  strips/<event>/annotations.json  the strings drawn in the strips' annotation bands
  replay/<provider>/<key>.json  the recorded model replies those calls got
```

Every file is small: strips are the runtime's JPEGs (about 35 KB each), never video.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from rulecam.contracts import Frozen, SitePlan, Track
from rulecam.contracts.events import EventRecord
from rulecam.demo.errors import FixtureError
from rulecam.eval.record import RunRecord
from rulecam.evidence.chain import read_chain
from rulecam.runtime.response import ResponseStatus

RUN_FILE = "run.json"
PLAN_FILE = "plan.json"
RESPONSE_FILE = "response.json"
CHAIN_FILE = "chain.jsonl"
WINDOWS_FILE = "windows.json"
STRIPS_DIR = "strips"
REPLAY_DIR = "replay"


class TrackWindow(Frozen):
    """The tracks one evaluation read: `TrackStore.window(camera_id, t0, t1)`."""

    camera_id: str
    t0: datetime
    t1: datetime
    tracks: tuple[Track, ...]


class TrackWindows(Frozen):
    """Every recorded window of a fixture (the contents of `windows.json`)."""

    windows: tuple[TrackWindow, ...] = ()


class RecordedWindows:
    """A read-only `TrackStore` that answers exactly the windows recorded in a fixture."""

    def __init__(self, windows: TrackWindows) -> None:
        """Index the windows by camera and bounds."""
        self._windows = {(w.camera_id, w.t0, w.t1): list(w.tracks) for w in windows.windows}

    def append(self, tracks: list[Track]) -> None:
        """Refuse writes: a fixture is evidence and never changes.

        Raises:
            FixtureError: Always.
        """
        msg = f"a golden fixture is read-only ({len(tracks)} tracks not stored)"
        raise FixtureError(msg)

    def window(self, camera_id: str, t0: datetime, t1: datetime) -> list[Track]:
        """Return the recorded window, or no tracks when it was not recorded."""
        return list(self._windows.get((camera_id, t0, t1), []))


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"golden fixture file {path} is missing or not JSON: {exc}"
        raise FixtureError(msg) from exc


class GoldenFixture:
    """A recorded-run fixture folder (layout in the module docstring)."""

    def __init__(self, root: Path) -> None:
        """Point at the folder; nothing is read until asked for."""
        self.root = root

    @property
    def chain_path(self) -> Path:
        """The day's chain file."""
        return self.root / CHAIN_FILE

    @property
    def replay_dir(self) -> Path:
        """The recorded model replies."""
        return self.root / REPLAY_DIR

    def run(self) -> RunRecord:
        """The eval harness's record of the live run.

        Raises:
            FixtureError: If it is missing or invalid.
        """
        try:
            return RunRecord.model_validate(_read_json(self.root / RUN_FILE))
        except ValidationError as exc:
            msg = f"{self.root / RUN_FILE} is not a run record: {exc}"
            raise FixtureError(msg) from exc

    def plan(self) -> SitePlan:
        """The plan the run approved.

        Raises:
            FixtureError: If it is missing or invalid.
        """
        try:
            return SitePlan.model_validate(_read_json(self.root / PLAN_FILE))
        except ValidationError as exc:
            msg = f"{self.root / PLAN_FILE} is not a site plan: {exc}"
            raise FixtureError(msg) from exc

    def response(self) -> ResponseStatus:
        """How the runtime's policy, firewall and actions were configured during the run.

        Raises:
            FixtureError: If it is missing or invalid.
        """
        try:
            return ResponseStatus.model_validate(_read_json(self.root / RESPONSE_FILE))
        except ValidationError as exc:
            msg = f"{self.root / RESPONSE_FILE} is not a response status: {exc}"
            raise FixtureError(msg) from exc

    def chain(self) -> list[EventRecord]:
        """Every record of the day's chain.

        Raises:
            FixtureError: If the file is missing or a line is malformed.
        """
        try:
            return read_chain(self.chain_path)
        except (OSError, ValueError, ValidationError) as exc:
            msg = f"{self.chain_path} is not a readable chain: {exc}"
            raise FixtureError(msg) from exc

    def run_records(self) -> list[EventRecord]:
        """The chain records of the run: its camera's candidates and everything about them."""
        run = self.run()
        records = self.chain()
        ids = {
            event_id
            for r in records
            if r.kind == "candidate" and _candidate_of(r, run) and (event_id := _event_id(r))
        }
        return [r for r in records if r.payload.get("event_id") in ids or _event_id(r) in ids]

    def windows(self) -> RecordedWindows:
        """The recorded track windows as a read-only track store.

        Raises:
            FixtureError: If `windows.json` is missing or invalid.
        """
        try:
            windows = TrackWindows.model_validate(_read_json(self.root / WINDOWS_FILE))
        except ValidationError as exc:
            msg = f"{self.root / WINDOWS_FILE} is not a set of track windows: {exc}"
            raise FixtureError(msg) from exc
        return RecordedWindows(windows)

    def strips(self, event_id: str) -> list[Path]:
        """The candidate's strip frames in order (`1.jpg`, `2.jpg`, …); empty when it had none."""
        folder = self.root / STRIPS_DIR / event_id
        frames = [p for p in folder.glob("*.jpg") if p.stem.isdigit()] if folder.is_dir() else []
        return sorted(frames, key=lambda p: int(p.stem))


def _event_id(record: EventRecord) -> str | None:
    candidate = record.payload.get("candidate")
    if isinstance(candidate, dict):
        value = candidate.get("event_id")
        return value if isinstance(value, str) else None
    return None


def _candidate_of(record: EventRecord, run: RunRecord) -> bool:
    """Whether a candidate record belongs to the run (its camera and plan version)."""
    candidate = record.payload.get("candidate")
    if not isinstance(candidate, dict) or candidate.get("camera_id") != run.camera_id:
        return False
    return run.plan_version_hash is None or candidate.get("plan_version_hash") == (
        run.plan_version_hash
    )
