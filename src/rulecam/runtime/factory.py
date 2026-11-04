"""Builds the real runtime from settings (task P3-08): SAM router, tracker, Parquet store, Glimmer.

`build_perception` wires the merged P1 modules: hosted SAM 3.1 through the replay cache, the
self-hosted worker (our own GPU pod when `SAM_BASE_URL` is set, otherwise Modal),
`SegmenterRouter`, `PerceptionMetrics`, and a thread-safe
`ParquetTrackStore` shared by the runtime loop and the correction loop. `build_runtime` adds the
`Tracker`, the Tier 1 Glimmer verifier, the response side (`runtime.response`: the
safety-asymmetric policy, the scene-text firewall and the actions) and the evidence chain.

`RULECAM_RUNTIME_SAM_MODE` picks the runtime perception. `image` (the default, PLAN-9.5 WS2b):
`KeyframeTracker` segments 2 to 4 keyframes per second per camera (`RULECAM_KEYFRAME_FPS`,
`RULECAM_KEYFRAME_FPS_CAMERAS`) with SAM 3.1 image mode, self-hosted (`POST /segment_image`
on our GPU pod) with hosted image requests as the fallback (`router.keyframe_chain`), and joins
them into tracks with ByteTrack; it is its own static cache, cost meter and budget estimate.
`video` keeps the chain below, which probes, Rule CI and evidence re-checks use in any mode.

In video mode the tracker's segmenter is, from the outside in: `StaticServedSegmenter`
(static concepts from the static cache, no SAM call) -> `CallMeter` (requests and billed frames
per camera, for the loop's budget and `/runtime/metrics`) -> `LimitedSegmenter` (at most
`RULECAM_SAM_MAX_CONCURRENCY` SAM calls open at once, queue time timed) -> the router's runtime
segmenter, whose hosted SAM returns boxes only unless `RULECAM_RUNTIME_SAM_MASKS` is set (mask
decoding is GIL-bound pure Python; the runtime needs foot points only). The
loop also gets the motion gate, the idle-sampling/budget policy and its pipeline depth
(`RULECAM_RUNTIME_PIPELINE_DEPTH`) from settings. With more than one chunk in flight the tracker
does not re-prompt: a re-prompt needs the previous chunk's tracks, which would make every chunk
wait for one more SAM round trip in order, so identities are handed off by IoU.

Each candidate gets its evidence strip from `EpisodeStrips` (P4-05): keyframes across the
episode from the chunks on disk, with the rule's boxes, zones and distance ruler, in the plan's
privacy mode, under `<data dir>/strips`. With `RULECAM_POSE_ONNX` (RTMPose-l body7, CPU) each
person in a strip gets its real pose (`pose.keypoints.load_pose_estimator`). The policy is
P3-05's `VerificationPolicy` and the actions P3-06's `ActionExecutor`, dry-run by default
(`RULECAM_ACTIONS_DRY_RUN`).

Crop-zoom (add-on 3.3, `RULECAM_CROP_ZOOM`, on by default): `crop_segmenter` runs SAM 3.1 on
enlarged head and hand crops, on the GPU pod's `/segment_crops` with hosted SAM as the fallback,
or hosted SAM alone without a pod. The planner's probe uses it for worn items, and `build_tier0`
makes it Tier 0 before Glimmer (not in replay mode, where it would change Glimmer's prompts).
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted.sam import HostedSam31
from rulecam.clients.meta_hosted.sam_wire import SAM_MODEL
from rulecam.clients.open_weights.glimmer import GlimmerClient, glimmer_headers
from rulecam.clients.transport import HttpxTransport, Transport
from rulecam.config import Settings
from rulecam.contracts import Chunk, Clock, EventRecord, Track
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.log import get_logger
from rulecam.perception.botsort import boxmot_available, by_concept, new_botsort_stream
from rulecam.perception.crop_zoom import CropSegmenter, FallbackCrops, LocalCrops, WorkerCrops
from rulecam.perception.errors import SamRuntimeError
from rulecam.perception.keyframe_tracker import KeyframeIdentities, KeyframeTracker, StreamTracker
from rulecam.perception.keyframes import HostedKeyframes, KeyframeRates, SelfHostedKeyframes
from rulecam.perception.metrics import Backend, PerceptionMetrics
from rulecam.perception.motion_gate import MotionGate
from rulecam.perception.point_map import WorkerGeometry
from rulecam.perception.router import (
    CallMeter,
    LimitedSegmenter,
    Purpose,
    RoutingPolicy,
    SegmenterRouter,
    keyframe_chain,
)
from rulecam.perception.sam_runtime import (
    HttpSamWorker,
    ModalSam31,
    ModalWorker,
    SamImageWorker,
    SamWorker,
)
from rulecam.perception.static_cache import StaticCache, StaticServedSegmenter
from rulecam.perception.tracking import Tracker
from rulecam.pose.keypoints import RtmposeOnnx, load_pose_estimator
from rulecam.replay.cache import ReplayCache
from rulecam.rules.extension import ExtensionHook
from rulecam.rules.operators import OperatorExclusion, is_person_like
from rulecam.rules.store import ParquetTrackStore
from rulecam.runtime.cost import CostMeter
from rulecam.runtime.loop import (
    BillEstimate,
    ChunkTracker,
    RuntimeLoop,
    SamUsage,
    StaticSink,
    chunk_end,
    video_bill_estimate,
)
from rulecam.runtime.response import Response, build_response
from rulecam.runtime.sampling import SamplingPolicy
from rulecam.runtime.serverless import endpoint_id
from rulecam.runtime.strips import EpisodeFrames, EpisodeStrips, face_detector
from rulecam.runtime.timings import StageTimings
from rulecam.verify.guard import BreakerPolicy, CircuitBreaker, GuardedVerifier, http_probe
from rulecam.verify.tier0 import CropZoomCheck
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
    """Remembers the most recent chunks of each camera (the correction loop's `ChunkSource`).

    With a `journal`, every recorded chunk is also appended to a JSON-lines file and the index
    is reloaded from it on start (chunks whose file is gone are skipped), so the recorded video
    stays reachable after a restart (`POST /ask` looks at it).
    """

    def __init__(
        self,
        metrics: PerceptionMetrics | None = None,
        per_camera: int = CHUNKS_PER_CAMERA,
        journal: Path | None = None,
    ) -> None:
        """Create the index, reloading the journal when there is one.

        Args:
            metrics: Receives `record_chunk` for every chunk (per-hour cost normalisation).
            per_camera: Chunks kept per camera; older ones are forgotten.
            journal: JSON-lines file of recorded chunks, appended to and reloaded on start.
        """
        self._metrics = metrics
        self._chunks: dict[str, deque[Chunk]] = defaultdict(lambda: deque(maxlen=per_camera))
        self._lock = threading.Lock()
        self._journal = journal
        if journal is not None and journal.is_file():
            for line in journal.read_text("utf-8").splitlines():
                try:
                    chunk = Chunk.model_validate_json(line)
                except ValueError:
                    continue
                if Path(chunk.path).is_file():
                    self._chunks[chunk.camera_id].append(chunk)

    def record(self, chunk: Chunk) -> None:
        """Add a chunk that just became ready."""
        with self._lock:
            self._chunks[chunk.camera_id].append(chunk)
            if self._journal is not None:
                self._journal.parent.mkdir(parents=True, exist_ok=True)
                with self._journal.open("a", encoding="utf-8") as out:
                    out.write(chunk.model_dump_json() + "\n")
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
    runtime_hosted: HostedSam31 | None = None  # the runtime path's view of `hosted`
    image_worker: SamImageWorker | None = None  # our GPU pod's `/segment_image`, if configured


def self_hosted_worker(settings: Settings) -> tuple[SamWorker, str] | None:
    """Pick the self-hosted SAM worker: our GPU pod (`SAM_BASE_URL`) first, then Modal.

    Args:
        settings: Application settings.

    Returns:
        The worker and a short name for the log, or `None` when neither is configured (runtime
        perception is then hosted).
    """
    if settings.sam_base_url and settings.sam_api_key is not None:
        return HttpSamWorker.from_settings(settings), "http"
    if settings.modal_token_id is not None and settings.modal_token_secret is not None:
        return ModalWorker(settings), "modal"
    return None


def build_perception(
    settings: Settings, *, cache: ReplayCache, clock: Clock, bus: Bus
) -> Perception:
    """Build SAM (hosted + self-hosted behind `SegmenterRouter`), metrics and the track store.

    Runtime perception is self-hosted when `SAM_BASE_URL` + `SAM_API_KEY` (our GPU pod) or the
    Modal tokens are configured, hosted otherwise. Probes, surveys and Rule CI stay hosted. A
    self-hosted failure sends runtime calls to hosted for the router's retry window; each such
    outage is logged (`sam_fallback_to_hosted`) and counted in `sam_fallbacks`.
    """
    metrics = PerceptionMetrics()
    hosted = hosted_sam(settings, cache, clock=clock, bus=bus)
    runtime_hosted = hosted.runtime_view(masks=settings.rulecam_runtime_sam_masks)
    picked = self_hosted_worker(settings)
    backend = Backend.SELF_HOSTED if picked is not None else Backend.HOSTED
    worker, worker_name = picked if picked is not None else (ModalWorker(settings), "none")

    def on_fallback(camera_id: str, error: SamRuntimeError) -> None:  # noqa: ARG001 - hook signature
        metrics.record_fallback(camera_id)

    router = SegmenterRouter(
        hosted,
        ModalSam31(worker, bus=bus, metrics=metrics),
        clock=clock,
        policy=RoutingPolicy(runtime_backend=backend),
        metrics=metrics,
        on_fallback=on_fallback,
        runtime_hosted=runtime_hosted,
    )
    store = LockedTrackStore(ParquetTrackStore(settings.rulecam_data_dir / "tracks"))
    _log.info("perception_built", runtime_backend=str(backend), self_hosted_worker=worker_name)
    return Perception(
        hosted=hosted,
        router=router,
        metrics=metrics,
        store=store,
        chunks=ChunkIndex(metrics, journal=settings.rulecam_data_dir / "chunks" / "index.jsonl"),
        runtime_hosted=runtime_hosted,
        image_worker=worker if picked is not None and isinstance(worker, HttpSamWorker) else None,
    )


@cache
def runtime_pose(model_path: Path | None, sha256: str) -> RtmposeOnnx | None:
    """The runtime's pose model, loaded once per file (strips and Tier 0 share it)."""
    return load_pose_estimator(model_path, sha256=sha256)


def crop_segmenter(settings: Settings, perception: Perception) -> CropSegmenter | None:
    """Crop-zoom SAM 3.1 (add-on 3.3): the GPU pod, falling back to hosted SAM; `None` when off.

    Off in replay mode too: its extra SAM calls were never recorded, and Tier 0's lines would
    change Glimmer's prompts, so a replay would no longer match its recording.

    Args:
        settings: Application settings (`rulecam_crop_zoom`, `rulecam_mode`).
        perception: From `build_perception` (the pod's client and hosted SAM).

    Returns:
        The crop segmenter, or `None` when `RULECAM_CROP_ZOOM` is off or the run is a replay.
    """
    if not settings.rulecam_crop_zoom or settings.rulecam_mode == "replay":
        return None
    hosted = LocalCrops(perception.hosted)
    worker = perception.image_worker
    if isinstance(worker, HttpSamWorker):
        return FallbackCrops(WorkerCrops(worker), hosted)
    return hosted


def geometry_estimator(settings: Settings, perception: Perception) -> WorkerGeometry | None:
    """MoGe-2 on the GPU pod for the planner's calibration (add-on 3.5), or `None`.

    Args:
        settings: Application settings (`rulecam_calibration_model`, `rulecam_mode`).
        perception: From `build_perception` (the pod's client).

    Returns:
        `None` when the setting is `off`, the run is a replay, or there is no GPU pod; else the
        estimator, which in `auto` mode asks the pod's `/health` once whether MoGe is installed.
    """
    mode = settings.rulecam_calibration_model
    worker = perception.image_worker
    if mode == "off" or settings.rulecam_mode == "replay" or not isinstance(worker, HttpSamWorker):
        return None
    return WorkerGeometry(worker, check_health=mode == "auto")


def build_tier0(settings: Settings, perception: Perception) -> CropZoomCheck | None:
    """Tier 0: crop-zoom findings on the rule's PPE regions, given to Glimmer as context.

    Args:
        settings: Application settings.
        perception: The chunk index, track store and SAM clients.

    Returns:
        The check, or `None` when crop-zoom is off or the run is a replay (`crop_segmenter`).
    """
    crops = crop_segmenter(settings, perception)
    if crops is None:
        return None
    return CropZoomCheck(
        frames=EpisodeFrames(perception.chunks),
        store=perception.store,
        crops=crops,
        pose=runtime_pose(settings.rulecam_pose_onnx, settings.rulecam_pose_sha256),
    )


def build_tier1(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    tier0: CropZoomCheck | None = None,
    probe: Callable[[], bool] | None = None,
) -> GuardedVerifier:
    """The Tier 1 verifier: Glimmer through the replay cache, behind retry and a circuit breaker.

    A failure never passes silently: it becomes a `verifier_error` verdict that the policy sends
    to a person (`verify.guard`). While the circuit is open, the API's ticker runs `probe` (the
    API passes Glimmer's dependency probe), by default `glimmer_probe`. With `tier0`
    (`build_tier0`), its crop-zoom findings are added to each prompt.
    """
    if settings.rulecam_mode == "replay":
        probe = None
    elif probe is None and endpoint_id(settings.glimmer_base_url) is None:
        probe = glimmer_probe(settings)
    client = GlimmerClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
    policy = BreakerPolicy(
        retries=settings.rulecam_verify_retries,
        failure_threshold=settings.rulecam_verify_breaker_failures,
        cooldown_s=settings.rulecam_verify_breaker_cooldown_s,
    )
    return GuardedVerifier(
        GlimmerVerifier(client, raise_errors=True, context=tier0),
        model_version=client.model,
        breaker=CircuitBreaker(policy),
        probe=probe,
    )


def glimmer_health_url(base_url: str) -> str:
    """llama-server's `/health` for an OpenAI-style base URL (`…/v1` → `…/health`)."""
    return base_url.rstrip("/").removesuffix("/v1") + "/health"


def glimmer_probe(settings: Settings) -> Callable[[], bool]:
    """Whether the Tier 1 server answers: llama-server's `/health`, else `GET /models` with key.

    A hosted OpenAI-compatible API (the Meta API serving Muse Spark as Tier 1) has no open
    `/health`; its `/models` answers 200 to a valid key.
    """
    health = http_probe(glimmer_health_url(settings.glimmer_base_url))
    models = http_probe(
        settings.glimmer_base_url.rstrip("/") + "/models", headers=glimmer_headers(settings)
    )
    return lambda: health() or models()


def build_runtime(
    settings: Settings,
    perception: Perception,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    chain: EventChain,
    response: Response | None = None,
    cost: CostMeter | None = None,
    extensions: ExtensionHook | None = None,
) -> RuntimeLoop:
    """Build the real runtime loop: router segmenter → `Tracker` → Parquet store → Glimmer.

    Args:
        settings: Application settings (Glimmer endpoint, evidence directory).
        perception: From `build_perception`.
        cache: The replay cache for Glimmer.
        clock: Time source.
        bus: The event bus.
        chain: The evidence chain.
        response: Policy, actions and Tier 1 (default: `build_tier1` + `build_response`).
        cost: The dollar budget; a camera it marks over budget degrades (`runtime.cost`).
        extensions: Runs approved model-written checks (`extensions.registry.RegistryHook`).

    Returns:
        The loop.
    """
    timings = StageTimings()
    picked = (
        _image_tracker(settings, perception, clock=clock, bus=bus, timings=timings)
        if settings.rulecam_runtime_sam_mode == "image"
        else _video_tracker(settings, perception, bus=bus, timings=timings)
    )
    _log.info("runtime_perception", mode=settings.rulecam_runtime_sam_mode)
    if response is None:
        response = build_response(
            settings,
            tier1=build_tier1(
                settings,
                cache=cache,
                clock=clock,
                bus=bus,
                tier0=build_tier0(settings, perception),
            ),
            segmenter=perception.hosted,
            cache=cache,
            clock=clock,
            bus=bus,
            chain=chain,
        )
    return RuntimeLoop(
        bus=bus,
        clock=clock,
        tracker=picked.tracker,
        store=perception.store,
        verifier=response.tier1,
        policy=response.policy,
        actions=response.actions,
        strips=EpisodeStrips(
            chunks=perception.chunks,
            store=perception.store,
            out_dir=settings.rulecam_data_dir / "strips",
            faces=face_detector(settings.rulecam_face_model),
            frames=settings.rulecam_strip_frames,
            max_width=settings.rulecam_strip_max_width,
            raw=settings.rulecam_prototype_mode,
            pose=runtime_pose(settings.rulecam_pose_onnx, settings.rulecam_pose_sha256),
        ),
        chain=chain,
        sampling=SamplingPolicy.from_settings(settings),
        motion_gate=MotionGate(),  # camera time is recorded by `ChunkIndex`, not twice here
        usage=picked.usage,
        static_sink=picked.static_sink,
        companions=(lambda: picked.static_cache.run(bus),),
        pipeline_depth=settings.rulecam_runtime_pipeline_depth,
        timings=timings,
        verify_workers=settings.rulecam_runtime_verify_workers,
        operators=OperatorExclusion.from_csv(
            settings.rulecam_operator_exclusion_off, warmup_s=settings.rulecam_operator_warmup_s
        ),
        over_budget=cost.over_budget if cost is not None else None,
        bill_estimate=picked.bill_estimate,
        extensions=extensions,
    )


@dataclass(frozen=True)
class RuntimeTracker:
    """The runtime loop's perception side, for one `RULECAM_RUNTIME_SAM_MODE`."""

    tracker: ChunkTracker
    usage: SamUsage
    static_sink: StaticSink
    static_cache: StaticCache
    bill_estimate: BillEstimate


def _video_tracker(
    settings: Settings, perception: Perception, *, bus: Bus, timings: StageTimings
) -> RuntimeTracker:
    """Video mode: SAM 3.1 video per chunk through the router, IoU hand-off between chunks."""
    if perception.runtime_hosted is not None:
        perception.runtime_hosted.set_stage_sink(timings.record)
    limited = LimitedSegmenter(
        perception.router.for_purpose(Purpose.RUNTIME),
        settings.rulecam_sam_max_concurrency,
        timings=timings,
    )
    meter = CallMeter(limited)
    served = StaticServedSegmenter(meter)
    tracker = Tracker(
        served,
        store=perception.store,
        bus=bus,
        reprompt=settings.rulecam_runtime_pipeline_depth == 1,
        max_workers=settings.rulecam_runtime_concept_workers,
    )
    return RuntimeTracker(
        tracker=tracker,
        usage=meter,
        static_sink=lambda concepts, version: served.set_static(concepts, generation=version),
        static_cache=served.cache,
        bill_estimate=video_bill_estimate,
    )


def person_tracker(
    settings: Settings, *, available: bool | None = None
) -> Callable[[float], StreamTracker] | None:
    """BoT-SORT for people (add-on 3.1), or `None` to keep ByteTrack (logged).

    Appearance re-ID of people runs only in prototype mode (the owner's analytics), never
    outside it, and needs BoxMOT (the `reid` extra) on this pod.

    Args:
        settings: Application settings (`RULECAM_PERSON_TRACKER`, prototype mode, the floor).
        available: Whether BoxMOT is installed (checked when `None`).

    Returns:
        A person tracker builder, or `None`.
    """
    if settings.rulecam_person_tracker != "botsort":
        return None
    if not settings.rulecam_prototype_mode:
        _log.info("person_tracker_bytetrack", reason="botsort re-ID runs in prototype mode only")
        return None
    if not (boxmot_available() if available is None else available):
        _log.warning(
            "person_tracker_bytetrack", reason="boxmot not installed (uv sync --extra reid)"
        )
        return None
    low = settings.rulecam_tracker_low_confidence
    return lambda lost_track_s: new_botsort_stream(lost_track_s, low_confidence=low)


def _image_tracker(
    settings: Settings, perception: Perception, *, clock: Clock, bus: Bus, timings: StageTimings
) -> RuntimeTracker:
    """Image mode: SAM 3.1 image keyframes (self-hosted, then hosted) joined by ByteTrack.

    People go on BoT-SORT with re-ID vectors when `person_tracker` allows it.
    """
    metrics = perception.metrics
    people = person_tracker(settings)
    hosted = HostedKeyframes(
        perception.runtime_hosted or perception.hosted,
        max_concurrency=settings.rulecam_sam_max_concurrency,
        metrics=metrics,
        timings=timings.record,
    )
    worker = perception.image_worker
    self_hosted = (
        SelfHostedKeyframes(
            worker,
            bus=bus,
            metrics=metrics,
            timings=timings.record,
            min_confidence=settings.rulecam_tracker_low_confidence,
            embed=is_person_like if people is not None else None,
        )
        if worker is not None
        else None
    )

    def on_fallback(camera_id: str, error: SamRuntimeError) -> None:  # noqa: ARG001 - hook signature
        metrics.record_fallback(camera_id)

    keyframes = keyframe_chain(
        hosted, self_hosted, policy=perception.router.policy, clock=clock, on_fallback=on_fallback
    )
    tracker = KeyframeTracker(
        keyframes,
        rates=KeyframeRates.parse(
            settings.rulecam_keyframe_fps, settings.rulecam_keyframe_fps_cameras
        ),
        store=perception.store,
        bus=bus,
        identities=KeyframeIdentities(
            low_confidence=settings.rulecam_tracker_low_confidence,
            new_tracker=by_concept(is_person_like, people),
        ),
        timings=timings,
    )
    return RuntimeTracker(
        tracker=tracker,
        usage=tracker,
        static_sink=lambda concepts, version: tracker.set_static(concepts, generation=version),
        static_cache=tracker.cache,
        bill_estimate=tracker.estimate,
    )
