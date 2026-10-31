"""What one eval run observed, saved as JSON so it can be re-scored offline (replay mode).

A `RunRecord` is one clip and one rule played through the API once: the chunk timeline of the
scored pass (which maps camera time back to clip seconds), the evidence-chain records of that
camera and plan (candidates, verdicts, actions, which embed every model reply), the bus frames
seen on `/ws`, and `/runtime/metrics` samples. `rulecam eval score` reads these files without
touching the network, which is the CI replay path.
"""

from __future__ import annotations

import bisect
import json
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import Field, ValidationError

from rulecam.contracts import Frozen
from rulecam.eval.errors import EvalError

RUN_SUFFIX = ".run.json"


class ChunkSlot(Frozen):
    """One uploaded chunk of the scored pass: its clip offset and its place on camera time."""

    index: int = Field(ge=0)
    chunk_id: str
    clip_start_s: float = Field(ge=0)
    duration_s: float = Field(gt=0)
    cam_start: datetime


class MetricsSample(Frozen):
    """One `/runtime/metrics` reading (the fields the scorecard uses)."""

    at: datetime
    lag_s: float = 0.0
    keyframe_mode: bool = False
    billed_frames: int = 0
    sam_calls: int = 0
    in_flight: int = 0
    chunks_processed: int = 0
    segment_latency_p95_s: float = 0.0


class BusFrame(Frozen):
    """One `/ws` frame and when the harness received it."""

    at: datetime
    topic: str
    payload: dict[str, object]


class PredictedBox(Frozen):
    """One runtime track sample, mapped to clip seconds (for tracking metrics only).

    `track` is the store's opaque track id; it is only compared with itself, never used to
    group anything about a person (rule R13).
    """

    clip_s: float
    track: str
    concept: str
    box_xyxy: tuple[float, float, float, float]


class RunRecord(Frozen):
    """Everything observed while playing one clip for one rule."""

    clip: str
    rule: str
    camera_id: str
    plan_id: str | None = None
    plan_version_hash: str | None = None
    activated: tuple[str, ...] = ()
    rule_hashes: tuple[str, ...] = ()
    refused: str | None = None
    error: str | None = None
    speed: float = Field(default=1.0, gt=0)
    started_at: datetime
    finished_at: datetime
    chunk_width: int | None = None
    chunk_height: int | None = None
    chunks: tuple[ChunkSlot, ...] = ()
    chain: tuple[dict[str, object], ...] = ()
    bus: tuple[BusFrame, ...] = ()
    metrics: tuple[MetricsSample, ...] = ()
    predicted: tuple[PredictedBox, ...] = ()

    @property
    def timeline(self) -> Timeline:
        """The scored pass's camera-time to clip-time mapping."""
        return Timeline(self.chunks)

    @property
    def camera_hours(self) -> float:
        """Camera time covered by the scored pass, in hours."""
        return sum(slot.duration_s for slot in self.chunks) / 3600.0


class Timeline:
    """Maps camera timestamps to clip seconds and back, chunk by chunk."""

    def __init__(self, chunks: tuple[ChunkSlot, ...]) -> None:
        """Index the chunks of one pass (in upload order)."""
        self._chunks = tuple(sorted(chunks, key=lambda c: c.cam_start))
        self._starts = [c.cam_start for c in self._chunks]

    def __bool__(self) -> bool:
        """Whether any chunk was played."""
        return bool(self._chunks)

    def to_clip_s(self, ts: datetime) -> float | None:
        """Return the clip second shown at camera time `ts`, or `None` outside the pass."""
        i = bisect.bisect_right(self._starts, ts) - 1
        if i < 0:
            return None
        slot = self._chunks[i]
        offset = (ts - slot.cam_start).total_seconds()
        if offset > slot.duration_s:
            return None
        return slot.clip_start_s + offset

    def to_camera(self, clip_s: float) -> datetime | None:
        """Return the camera time at which clip second `clip_s` was shown, or `None`."""
        for slot in self._chunks:
            if slot.clip_start_s <= clip_s <= slot.clip_start_s + slot.duration_s:
                return slot.cam_start + timedelta(seconds=clip_s - slot.clip_start_s)
        return None


def run_file_name(clip: str, rule: str) -> str:
    """Return the file name of a run record."""
    return f"{clip}__{rule}{RUN_SUFFIX}"


def save_run(record: RunRecord, out_dir: Path) -> Path:
    """Write a run record as pretty JSON and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / run_file_name(record.clip, record.rule)
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_runs(runs_dir: Path) -> list[RunRecord]:
    """Read every `*.run.json` in a folder, sorted by file name.

    Raises:
        EvalError: If the folder has none or one is malformed.
    """
    paths = sorted(runs_dir.glob(f"*{RUN_SUFFIX}"))
    if not paths:
        msg = f"no *{RUN_SUFFIX} files in {runs_dir}"
        raise EvalError(msg)
    runs = []
    for path in paths:
        try:
            runs.append(RunRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, ValidationError) as exc:
            msg = f"{path}: {exc}"
            raise EvalError(msg) from exc
    return runs
