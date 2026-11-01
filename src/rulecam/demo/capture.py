"""Freeze a live record run into a golden fixture (`rulecam golden capture`, run on the API pod).

The run must have been played with the API in `RULECAM_MODE=record`, so every Tier 1 reply is in
the API's replay directory. Capture copies, for the run's camera and plan only:

- the eval run record, the approved plan and the runtime's response configuration;
- the whole day's chain file (the verify page and `verify_chain` check it from its first record);
- each candidate's track window, read back from the track store;
- each candidate's strip JPEGs, byte for byte;
- the recorded Glimmer reply of each candidate, found by rebuilding the request the runtime sent
  (a missing reply means the run was not recorded, and capture fails rather than guess).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rulecam.contracts import TrackStore
from rulecam.contracts.events import EventRecord
from rulecam.demo.errors import CaptureError
from rulecam.demo.fixture import (
    CHAIN_FILE,
    PLAN_FILE,
    RESPONSE_FILE,
    RUN_FILE,
    STRIPS_DIR,
    WINDOWS_FILE,
    GoldenFixture,
    TrackWindow,
    TrackWindows,
)
from rulecam.demo.tier1 import replay_tier1, with_strip
from rulecam.replay.cache import CacheEntry, ReplayCache, atomic_write_text
from rulecam.replay.errors import ReplayMiss
from rulecam.rules.backtest import read_candidate_payload

DEFAULT_MODEL = "muse-glimmer-30b"


class HarvestingCache(ReplayCache):
    """A replay-mode cache over the API's replies that copies every entry it serves elsewhere."""

    def __init__(self, source: Path, target: Path) -> None:
        """Read entries from `source`, copy the ones looked up to `target`."""
        super().__init__(source, "replay", clock=_NoClock())
        self._target = target

    def lookup(self, provider: str, model: str, request: Mapping[str, object]) -> CacheEntry:
        """Serve the entry and copy its file into the target directory."""
        entry = super().lookup(provider, model, request)
        destination = self._target / provider / f"{entry.key}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.entry_path(provider, entry.key), destination)
        return entry


class _NoClock:
    def now(self) -> datetime:  # pragma: no cover - replay mode never stamps entries
        msg = "a harvesting cache never records"
        raise CaptureError(msg)


@dataclass(frozen=True)
class CaptureSources:
    """Where the live run's data lives on the API pod.

    Attributes:
        run: The eval harness's `*.run.json`.
        plan: The approved plan as JSON (`GET /plans/{id}`).
        response: `GET /runtime/metrics` as JSON (its `response` block is kept).
        chain: The day's chain file (`<data>/evidence/chain/<site>/<day>.jsonl`).
        tracks: The API's track store.
        strips: The strip root (`<data>/strips`).
        replay: The API's replay directory (`<data>/replay`).
    """

    run: Path
    plan: Path
    response: Path
    chain: Path
    tracks: TrackStore
    strips: Path
    replay: Path


def _write_json(path: Path, data: object) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise CaptureError(msg) from exc


def _copy_files(sources: CaptureSources, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sources.run, out / RUN_FILE)
    shutil.copyfile(sources.chain, out / CHAIN_FILE)
    _write_json(out / PLAN_FILE, _load_json(sources.plan))
    metrics = _load_json(sources.response)
    response = metrics.get("response") if isinstance(metrics, dict) else None
    if not isinstance(response, dict):
        msg = f"{sources.response} has no `response` block (is it /runtime/metrics?)"
        raise CaptureError(msg)
    _write_json(out / RESPONSE_FILE, response)


def capture(sources: CaptureSources, out: Path) -> GoldenFixture:
    """Build the fixture folder `out` from a recorded live run.

    Args:
        sources: The run's files and stores on the API pod.
        out: The fixture folder (created; files in it are replaced).

    Returns:
        The fixture.

    Raises:
        CaptureError: If an input is unreadable, a candidate has no strip or no recorded track
            window, or a Tier 1 reply was not recorded.
    """
    _copy_files(sources, out)
    fixture = GoldenFixture(out)
    records = fixture.run_records()
    windows: list[TrackWindow] = []
    harvest = HarvestingCache(sources.replay, fixture.replay_dir)
    for record in records:
        if record.kind != "candidate":
            continue
        candidate, context = read_candidate_payload(record)
        tracks = sources.tracks.window(candidate.camera_id, context.t0, context.t1)
        if not tracks:
            msg = f"no tracks stored for {candidate.event_id} ({context.t0} to {context.t1})"
            raise CaptureError(msg)
        windows.append(
            TrackWindow(camera_id=candidate.camera_id, t0=context.t0, t1=context.t1, tracks=tracks)
        )
        strip_dir = sources.strips / candidate.camera_id / candidate.event_id
        frames = sorted(
            (p for p in strip_dir.glob("*.jpg") if p.stem.isdigit()), key=lambda p: int(p.stem)
        )
        if not frames:
            msg = f"no strip for {candidate.event_id} under {strip_dir}"
            raise CaptureError(msg)
        target = out / STRIPS_DIR / candidate.event_id
        target.mkdir(parents=True, exist_ok=True)
        for frame in frames:
            shutil.copyfile(frame, target / frame.name)
        model = _model(records, candidate.event_id)
        try:
            replay_tier1(
                with_strip(candidate, fixture.strips(candidate.event_id)),
                context.rule,
                model=model,
                cache=harvest,
            )
        except ReplayMiss as exc:
            msg = (
                f"Tier 1 reply for {candidate.event_id} was not recorded; play the run with the "
                f"API in RULECAM_MODE=record ({exc})"
            )
            raise CaptureError(msg) from exc
    _write_json(out / WINDOWS_FILE, TrackWindows(windows=tuple(windows)).model_dump(mode="json"))
    return fixture


def _model(records: list[EventRecord], event_id: str) -> str:
    """The Glimmer model the runtime asked about this candidate (from its chained verdict)."""
    for record in records:
        payload = record.payload
        if (
            record.kind == "verdict"
            and payload.get("event_id") == event_id
            and payload.get("tier") == "glimmer"
        ):
            return str(payload.get("model_version") or DEFAULT_MODEL)
    return DEFAULT_MODEL
