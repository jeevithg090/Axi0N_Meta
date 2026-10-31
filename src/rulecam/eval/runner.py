"""Play eval clips into a running API as paired-phone cameras (`rulecam eval run`).

One run is one clip and one rule, the way the pod's test feeder worked:

1. split the clip into 3 s H.264 MP4 chunks (`rulecam.capture.chunker.ffmpeg_args`);
2. pair as a phone (`POST /pair/token` over loopback, then claim it);
3. **survey pass**: upload the chunks in a loop at the chosen pace for `survey_s`, so the Site
   Planner has recent frames of this camera, then submit the request and keep feeding until the
   plan is ready;
4. approve it (a 409 is recorded as an honest refusal);
5. **scored pass**: upload the clip once from the start, recording where each chunk lands on
   camera time (the server places a chunk so it ends when it arrived, never before the previous
   one: `pair_phone.service.chunk_start`, replicated here on the same clock);
6. drain until nothing is in flight, then collect the evidence-chain records of this camera and
   plan, the bus frames, the `/runtime/metrics` samples, and optionally the track store.

The pairing endpoints take one request per second per code, so the fastest pace is about 3x
real time (`speed`); latency and lag are only meaningful at `speed = 1`.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from rulecam.capture.chunker import ffmpeg_args, probe_chunk
from rulecam.capture.pair_phone.service import chunk_start
from rulecam.contracts import RuleIR, Track
from rulecam.eval.client import ApiClient, BusListener, NoBus, Pairing, PlanRefusedError
from rulecam.eval.errors import EvalRunError
from rulecam.eval.record import (
    ChunkSlot,
    MetricsSample,
    PredictedBox,
    RunRecord,
    Timeline,
    save_run,
)
from rulecam.eval.schema import ClipEntry, RuleLabels
from rulecam.log import get_logger
from rulecam.rules.hashing import rule_ir_hash

_log = get_logger(__name__)

CHUNK_SECONDS = 3.0
CHUNK_FPS = 10.0
MIN_UPLOAD_INTERVAL_S = 1.05  # the pairing endpoints allow one request per second per code
MAX_SPEED = CHUNK_SECONDS / MIN_UPLOAD_INTERVAL_S
SPLIT_TIMEOUT_S = 600.0


@dataclass(frozen=True)
class LocalChunk:
    """One chunk file of the clip and its clip offset."""

    index: int
    path: Path
    clip_start_s: float
    duration_s: float


Splitter = Callable[[Path, Path], list[LocalChunk]]


def split_clip(clip: Path, out_dir: Path) -> list[LocalChunk]:
    """Cut a clip into 3 s, 10 fps H.264 faststart chunks, as a phone would send them.

    Raises:
        EvalRunError: If ffmpeg fails or produces nothing.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    args = ffmpeg_args(str(clip), out_dir / "c%04d.mp4", fps=CHUNK_FPS, chunk_seconds=CHUNK_SECONDS)
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=SPLIT_TIMEOUT_S)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as exc:
        msg = f"could not split {clip}: {exc}"
        raise EvalRunError(msg) from exc
    chunks, offset = [], 0.0
    for index, path in enumerate(sorted(out_dir.glob("c*.mp4"))):
        info = probe_chunk(path)
        duration = info.n_frames / info.fps if info.fps > 0 else CHUNK_SECONDS
        chunks.append(LocalChunk(index, path, offset, duration))
        offset += duration
    if not chunks:
        msg = f"ffmpeg produced no chunks from {clip}"
        raise EvalRunError(msg)
    return chunks


class TrackSource(Protocol):
    """Reads the runtime's tracks (the API host's track store)."""

    def window(self, camera_id: str, t0: datetime, t1: datetime) -> list[Track]:
        """Tracks of a camera with their points in `[t0, t1]`."""
        ...


@dataclass(frozen=True)
class RunConfig:
    """How a run is paced and how long it may wait."""

    speed: float = 1.0
    survey_s: float = 80.0
    plan_timeout_s: float = 900.0
    plan_poll_s: float = 10.0
    settle_s: float = 15.0
    drain_timeout_s: float = 300.0

    def __post_init__(self) -> None:
        """Reject a pace the pairing rate limit cannot sustain."""
        if not 0 < self.speed <= MAX_SPEED:
            msg = f"speed must be in (0, {MAX_SPEED:.2f}]"
            raise ValueError(msg)


@dataclass
class Clock:
    """Wall clock, monotonic clock and sleep, injectable for tests."""

    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


def _metrics_sample(raw: dict[str, object], at: datetime) -> MetricsSample:
    fields = MetricsSample.model_fields.keys() - {"at"}
    try:
        return MetricsSample.model_validate({"at": at, **{k: raw[k] for k in fields if k in raw}})
    except ValidationError:
        return MetricsSample(at=at)


def _rule_hashes(plan: dict[str, object] | None, activated: Sequence[str]) -> tuple[str, ...]:
    rules = plan.get("rules") if plan else None
    out = []
    for raw in rules if isinstance(rules, list) else []:
        try:
            rule = RuleIR.model_validate(raw)
        except ValidationError:
            continue
        if rule.id in activated:
            out.append(rule_ir_hash(rule))
    return tuple(sorted(out))


def _days(start: datetime, end: datetime) -> Iterator[date]:
    day = start.date()
    while day <= end.date():
        yield day
        day += timedelta(days=1)


def relevant_records(
    records: Sequence[dict[str, object]], camera_id: str, since: datetime
) -> list[dict[str, object]]:
    """Keep the candidates of `camera_id` written since `since`, and the records about them."""
    events: set[str] = set()
    out = []
    for record in records:
        ts = record.get("ts")
        if not isinstance(ts, str) or datetime.fromisoformat(ts) < since:
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        candidate = payload.get("candidate")
        if record.get("kind") == "candidate" and isinstance(candidate, dict):
            if candidate.get("camera_id") == camera_id:
                events.add(str(candidate.get("event_id")))
                out.append(record)
        elif payload.get("event_id") in events:
            out.append(record)
    return out


class EvalRunner:
    """Plays clips through an API and records what happened."""

    def __init__(
        self,
        api: ApiClient,
        *,
        config: RunConfig | None = None,
        clock: Clock | None = None,
        splitter: Splitter | None = None,
        bus: Callable[[], BusListener] = NoBus,
        tracks: TrackSource | None = None,
    ) -> None:
        """Wire the runner.

        Args:
            api: The API client.
            config: Pace and timeouts.
            clock: Time sources (tests pass fakes).
            splitter: Cuts a clip into chunk files (`split_clip` by default).
            bus: Makes a fresh bus listener per run.
            tracks: The API host's track store, for tracking metrics (optional).
        """
        self._api = api
        self._config = config or RunConfig()
        self._clock = clock or Clock()
        self._split = splitter or split_clip
        self._bus = bus
        self._tracks = tracks
        self._last_end: datetime | None = None
        self._last_upload = float("-inf")

    @property
    def _interval_s(self) -> float:
        return max(CHUNK_SECONDS / self._config.speed, MIN_UPLOAD_INTERVAL_S)

    def run(self, entry: ClipEntry, rule: RuleLabels, clip: Path, work_dir: Path) -> RunRecord:
        """Play one clip for one rule and return the record (refusals and errors included)."""
        chunks = self._split(clip, work_dir / f"{entry.name}__{rule.id}")
        started = self._clock.now()
        self._last_end, self._last_upload = None, self._clock.monotonic()
        pairing = self._api.pair(entry.camera_id)
        listener = self._bus()
        listener.start()
        base = {"clip": entry.name, "rule": rule.id, "camera_id": pairing.camera_id}
        _log.info("eval_run_start", clip=entry.name, rule=rule.id, camera_id=pairing.camera_id)
        try:
            plan_id = self._plan(pairing, chunks, rule)
            plan = self._api.plan(plan_id)
            approved = self._api.approve(plan_id)
        except PlanRefusedError as exc:
            return self._finish(base, started, listener, refused=str(exc))
        except EvalRunError as exc:
            _log.warning("eval_run_failed", clip=entry.name, rule=rule.id, error=str(exc))
            return self._finish(base, started, listener, error=str(exc))
        raw_activated = approved.get("activated")
        activated = tuple(map(str, raw_activated)) if isinstance(raw_activated, list) else ()
        plan_hash = str(approved.get("plan_version_hash", ""))
        before = self._sample()
        slots, acks, samples = self._scored_pass(pairing, chunks)
        samples = [before, *samples, *self._drain()]
        predicted = self._predicted(pairing.camera_id, slots)
        return self._finish(
            {
                **base,
                "plan_id": plan_id,
                "plan_version_hash": plan_hash or None,
                "activated": activated,
                "rule_hashes": _rule_hashes(plan, activated),
                "chunks": tuple(slots),
                "metrics": tuple(samples),
                "predicted": predicted,
                "chunk_width": acks[0][0] if acks else None,
                "chunk_height": acks[0][1] if acks else None,
            },
            started,
            listener,
        )

    def _finish(
        self,
        fields: Mapping[str, object],
        started: datetime,
        listener: BusListener,
        **extra: object,
    ) -> RunRecord:
        finished = self._clock.now()
        chain: list[dict[str, object]] = []
        if "chunks" in fields:
            for day in _days(started, finished):
                chain += self._api.chain(day)
        return RunRecord.model_validate(
            {
                **fields,
                **extra,
                "speed": self._config.speed,
                "started_at": started,
                "finished_at": finished,
                "chain": relevant_records(chain, str(fields["camera_id"]), started),
                "bus": listener.stop(),
            }
        )

    def _upload(self, pairing: Pairing, chunk: LocalChunk) -> tuple[ChunkSlot, tuple[int, int]]:
        wait = self._last_upload + self._interval_s - self._clock.monotonic()
        if wait > 0:
            self._clock.sleep(wait)
        self._last_upload = self._clock.monotonic()
        ack = self._api.upload(pairing, chunk.path)
        start = chunk_start(self._clock.now(), timedelta(seconds=chunk.duration_s), self._last_end)
        self._last_end = start + timedelta(seconds=chunk.duration_s)
        slot = ChunkSlot(
            index=chunk.index,
            chunk_id=ack.chunk_id,
            clip_start_s=chunk.clip_start_s,
            duration_s=chunk.duration_s,
            cam_start=start,
        )
        return slot, (ack.width, ack.height)

    def _plan(self, pairing: Pairing, chunks: Sequence[LocalChunk], rule: RuleLabels) -> str:
        """Survey pass, request, and keep feeding until the plan is ready."""
        clock, config = self._clock, self._config
        survey_end = clock.monotonic() + config.survey_s
        plan_id: str | None = None
        deadline = next_poll = float("inf")
        while True:
            for chunk in chunks:
                self._upload(pairing, chunk)
                now = clock.monotonic()
                if plan_id is None and now >= survey_end:
                    plan_id = self._api.request_plan(rule.request, rule.lang)
                    deadline, next_poll = now + config.plan_timeout_s, now + config.plan_poll_s
                    _log.info("eval_plan_requested", plan_id=plan_id, rule=rule.id)
                elif plan_id is not None and now >= next_poll:
                    if self._api.plan(plan_id) is not None:
                        return plan_id
                    if now >= deadline:
                        msg = f"plan {plan_id} not ready after {config.plan_timeout_s:g} s"
                        raise EvalRunError(msg)
                    next_poll = now + config.plan_poll_s

    def _scored_pass(
        self, pairing: Pairing, chunks: Sequence[LocalChunk]
    ) -> tuple[list[ChunkSlot], list[tuple[int, int]], list[MetricsSample]]:
        slots, sizes, samples = [], [], []
        for chunk in chunks:
            slot, size = self._upload(pairing, chunk)
            slots.append(slot)
            sizes.append(size)
            samples.append(self._sample())
        return slots, sizes, samples

    def _sample(self) -> MetricsSample:
        return _metrics_sample(self._api.runtime_metrics(), self._clock.now())

    def _drain(self) -> list[MetricsSample]:
        """Sample metrics until nothing is in flight for `settle_s` (or the drain times out)."""
        clock, config = self._clock, self._config
        deadline = clock.monotonic() + config.drain_timeout_s
        idle_since: float | None = None
        samples = []
        while clock.monotonic() < deadline:
            sample = self._sample()
            samples.append(sample)
            now = clock.monotonic()
            if sample.in_flight:
                idle_since = None
            elif idle_since is None:
                idle_since = now
            elif now - idle_since >= config.settle_s:
                break
            clock.sleep(1.0)
        return samples

    def _predicted(self, camera_id: str, slots: Sequence[ChunkSlot]) -> tuple[PredictedBox, ...]:
        if self._tracks is None or not slots:
            return ()
        timeline = Timeline(tuple(slots))
        t0 = slots[0].cam_start
        t1 = slots[-1].cam_start + timedelta(seconds=slots[-1].duration_s)
        out = []
        for track in self._tracks.window(camera_id, t0, t1):
            for point in track.points:
                clip_s = timeline.to_clip_s(point.ts)
                if clip_s is not None:
                    out.append(
                        PredictedBox(
                            clip_s=clip_s,
                            track=track.track_id,
                            concept=track.concept,
                            box_xyxy=point.box_xyxy,
                        )
                    )
        return tuple(out)


def run_eval_set(
    runner: EvalRunner,
    clips: Sequence[tuple[ClipEntry, Path]],
    *,
    work_dir: Path,
    runs_dir: Path,
    include_unlabelled: bool = False,
) -> list[Path]:
    """Run every (clip, rule) in turn and save each record; return the record paths.

    Approving a plan replaces the runtime's active plan, so runs never overlap. Unlabelled rules
    are skipped unless asked for (they only feed Glimmer decisiveness).
    """
    saved = []
    for entry, video in clips:
        for rule in entry.rules:
            if rule.status == "unlabelled" and not include_unlabelled:
                continue
            record = runner.run(entry, rule, video, work_dir)
            saved.append(save_run(record, runs_dir))
    return saved
