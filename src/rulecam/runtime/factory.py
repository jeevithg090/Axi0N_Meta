"""Builds the real runtime from settings (task P3-08): SAM router, tracker, Parquet store, Glimmer.

`build_perception` wires the merged P1 modules: hosted SAM 3.1 through the replay cache, the
self-hosted Modal worker, `SegmenterRouter`, `PerceptionMetrics`, and a thread-safe
`ParquetTrackStore` shared by the runtime loop and the correction loop. `build_runtime` adds the
`Tracker`, the Tier 1 Glimmer verifier and the evidence chain around them.

On the runtime path the tracker's segmenter is, from the outside in: `StaticServedSegmenter`
(static concepts from the static cache, no SAM call) -> `CallMeter` (requests and billed frames
per camera, for the loop's budget and `/runtime/metrics`) -> the router's runtime segmenter. The
loop also gets the motion gate and the idle-sampling/budget policy from settings.

The policy (P3-05), actions (P3-06) and strip renderer (P4-05) have not landed yet, so the
defaults here are deliberately conservative: nothing acts first, nothing is executed, and no
pixels are attached to a candidate (so nothing raw can leave the loop in any privacy mode).
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted.sam import HostedSam31
from rulecam.clients.meta_hosted.sam_wire import SAM_MODEL
from rulecam.clients.open_weights.glimmer import GlimmerClient
from rulecam.clients.transport import HttpxTransport, Transport
from rulecam.config import Settings
from rulecam.contracts import (
    ActionRecord,
    CandidateEvent,
    Chunk,
    Clock,
    EventRecord,
    PrivacyMode,
    RuleIR,
    Track,
    VerifierVerdict,
)
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.log import get_logger
from rulecam.perception.metrics import Backend, PerceptionMetrics
from rulecam.perception.motion_gate import MotionGate
from rulecam.perception.router import CallMeter, Purpose, RoutingPolicy, SegmenterRouter
from rulecam.perception.sam_runtime import ModalSam31, ModalWorker
from rulecam.perception.static_cache import StaticServedSegmenter
from rulecam.perception.tracking import Tracker
from rulecam.replay.cache import ReplayCache
from rulecam.rules.store import ParquetTrackStore
from rulecam.runtime.loop import RuntimeLoop, chunk_end
from rulecam.runtime.sampling import SamplingPolicy
from rulecam.verify.decision import Decision
from rulecam.verify.tier1 import GlimmerVerifier

_log = get_logger(__name__)

SAM_PROVIDER = "meta"
CHUNKS_PER_CAMERA = 1200  # one hour of 3 s chunks


class LockedTrackStore:
    """A `TrackStore` that serialises calls, so worker threads can share one DuckDB connection."""

    def __init__(self, inner: ParquetTrackStore) -> None:
        """Wrap `inner`."""
        self._inner = inner
        self._lock = threading.Lock()

    def append(self, tracks: list[Track]) -> None:
        """Persist tracks."""
        with self._lock:
            self._inner.append(tracks)

    def window(self, camera_id: str, t0: datetime, t1: datetime) -> list[Track]:
        """Return the camera's tracks with points inside `[t0, t1]`."""
        with self._lock:
            return self._inner.window(camera_id, t0, t1)


class ChunkIndex:
    """Remembers the most recent chunks of each camera (the correction loop's `ChunkSource`)."""

    def __init__(
        self, metrics: PerceptionMetrics | None = None, per_camera: int = CHUNKS_PER_CAMERA
    ) -> None:
        """Create an empty index.

        Args:
            metrics: Receives `record_chunk` for every chunk (per-hour cost normalisation).
            per_camera: Chunks kept per camera; older ones are forgotten.
        """
        self._metrics = metrics
        self._chunks: dict[str, deque[Chunk]] = defaultdict(lambda: deque(maxlen=per_camera))
        self._lock = threading.Lock()

    def record(self, chunk: Chunk) -> None:
        """Add a chunk that just became ready."""
        with self._lock:
            self._chunks[chunk.camera_id].append(chunk)
        if self._metrics is not None:
            self._metrics.record_chunk(chunk.camera_id, chunk.n_frames / chunk.fps)

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        """Return the camera's chunks overlapping `[t0, t1]`, oldest first."""
        with self._lock:
            known = list(self._chunks.get(camera_id, ()))
        return [c for c in known if c.start_ts <= t1 and chunk_end(c) >= t0]

    def last_seen(self) -> dict[str, datetime]:
        """Return each camera's latest chunk start."""
        with self._lock:
            return {cam: q[-1].start_ts for cam, q in self._chunks.items() if q}


@dataclass(frozen=True)
class ChainEventSource:
    """Finds a candidate's chain record by `event_id` (the correction loop's `EventSource`)."""

    chain_root: Path
    site: str

    def find(self, event_id: str) -> EventRecord | None:
        """Return the newest `candidate` record for `event_id`, or `None`."""
        folder = self.chain_root / self.site
        files = sorted(folder.glob("*.jsonl"), reverse=True) if folder.is_dir() else []
        for path in files:
            for record in reversed(read_chain(path)):
                if record.kind == "candidate" and _candidate_event_id(record) == event_id:
                    return record
        return None


def _candidate_event_id(record: EventRecord) -> object:
    """The event id of a candidate record written by `candidate_payload` (P3-07b)."""
    candidate = record.payload.get("candidate")
    return candidate.get("event_id") if isinstance(candidate, dict) else None


class VerifyOnlyPolicy:
    """Placeholder for P3-05: never acts first; alerts only on a Tier 1 violation, via a human."""

    def act_first(self, candidate: CandidateEvent, rule: RuleIR) -> bool:  # noqa: ARG002 - protocol signature
        """Nothing acts before verification until the real policy lands."""
        return False

    def decide(self, candidate: CandidateEvent, rule: RuleIR, tier1: VerifierVerdict) -> Decision:  # noqa: ARG002 - protocol signature
        """Alert on a Tier 1 violation and route it to the human queue."""
        return Decision(
            alert=tier1.verdict == "violation",
            route="human_queue",
            act_first=False,
            reason=f"tier1 {tier1.verdict} (placeholder policy, P3-05 pending)",
        )


class NoActions:
    """Placeholder for P3-06: runs nothing."""

    def run(
        self,
        candidate: CandidateEvent,  # noqa: ARG002 - protocol signature
        rule: RuleIR,  # noqa: ARG002 - protocol signature
        decision: Decision,  # noqa: ARG002 - protocol signature
    ) -> list[ActionRecord]:
        """Run no action."""
        return []


class NoStrips:
    """Placeholder for P4-05: attaches no frames, so no pixels leave the loop in any mode."""

    def render(self, candidate: CandidateEvent, chunk: Chunk, mode: PrivacyMode) -> CandidateEvent:  # noqa: ARG002 - protocol signature
        """Return the candidate with an empty strip."""
        return candidate.model_copy(update={"strip_paths": ()})


def _offline(request: Mapping[str, object]) -> Mapping[str, object]:  # noqa: ARG001 - protocol signature
    msg = "no META_API_BASE_URL/META_API_KEY: hosted SAM can only be served from the replay cache"
    raise ClientConfigError(msg)


def hosted_sam(settings: Settings, cache: ReplayCache, *, clock: Clock, bus: Bus) -> HostedSam31:
    """Build hosted SAM 3.1 routed through the replay cache (offline when there are no keys).

    Args:
        settings: Application settings.
        cache: The replay cache; in replay mode a miss raises `ReplayMiss`, never calls HTTP.
        clock: Time source.
        bus: Bus for `relay.model_used`.

    Returns:
        The client.
    """
    http: Transport = _offline
    if settings.meta_api_base_url and settings.meta_api_key is not None:
        http = HttpxTransport(settings.meta_api_base_url, settings.meta_api_key.get_secret_value())
    return HostedSam31(cache.wrap_transport(SAM_PROVIDER, SAM_MODEL, http), clock=clock, bus=bus)


@dataclass(frozen=True)
class Perception:
    """The perception services shared by the runtime loop, correction loop and Pair-a-Phone."""

    hosted: HostedSam31
    router: SegmenterRouter
    metrics: PerceptionMetrics
    store: LockedTrackStore
    chunks: ChunkIndex


def build_perception(
    settings: Settings, *, cache: ReplayCache, clock: Clock, bus: Bus
) -> Perception:
    """Build SAM (hosted + self-hosted behind `SegmenterRouter`), metrics and the track store.

    Runtime perception is self-hosted when Modal tokens are configured, hosted otherwise.
    """
    metrics = PerceptionMetrics()
    hosted = hosted_sam(settings, cache, clock=clock, bus=bus)
    has_modal = settings.modal_token_id is not None and settings.modal_token_secret is not None
    backend = Backend.SELF_HOSTED if has_modal else Backend.HOSTED
    router = SegmenterRouter(
        hosted,
        ModalSam31(ModalWorker(settings), bus=bus, metrics=metrics),
        clock=clock,
        policy=RoutingPolicy(runtime_backend=backend),
        metrics=metrics,
    )
    store = LockedTrackStore(ParquetTrackStore(settings.rulecam_data_dir / "tracks"))
    _log.info("perception_built", runtime_backend=str(backend))
    return Perception(
        hosted=hosted, router=router, metrics=metrics, store=store, chunks=ChunkIndex(metrics)
    )


def build_runtime(
    settings: Settings,
    perception: Perception,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    chain: EventChain,
) -> RuntimeLoop:
    """Build the real runtime loop: router segmenter → `Tracker` → Parquet store → Glimmer.

    Args:
        settings: Application settings (Glimmer endpoint, evidence directory).
        perception: From `build_perception`.
        cache: The replay cache for Glimmer.
        clock: Time source.
        bus: The event bus.
        chain: The evidence chain.

    Returns:
        The loop, with placeholder policy, actions and strips until P3-05, P3-06 and P4-05.
    """
    meter = CallMeter(perception.router.for_purpose(Purpose.RUNTIME))
    served = StaticServedSegmenter(meter)
    tracker = Tracker(
        served,
        store=perception.store,
        bus=bus,
        max_workers=settings.rulecam_runtime_concept_workers,
    )
    verifier = GlimmerVerifier(
        GlimmerClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
    )
    return RuntimeLoop(
        bus=bus,
        clock=clock,
        tracker=tracker,
        store=perception.store,
        verifier=verifier,
        policy=VerifyOnlyPolicy(),
        actions=NoActions(),
        strips=NoStrips(),
        chain=chain,
        sampling=SamplingPolicy.from_settings(settings),
        motion_gate=MotionGate(),  # camera time is recorded by `ChunkIndex`, not twice here
        usage=meter,
        static_sink=lambda concepts, version: served.set_static(concepts, generation=version),
        companions=(lambda: served.cache.run(bus),),
    )
