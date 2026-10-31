"""The runtime orchestrator (task P3-08): one chunk in, chained and published decisions out.

For every `chunk.ready`:

1. pick the active rules that run on the chunk's camera (`ActivePlan.rule_cameras`, from the
   plan's feasibility evidence and zones); a camera with none is not segmented at all;
2. decide whether to segment the chunk (backpressure, idle sampling and the SAM budget of
   `runtime.sampling`), and which concepts (the motion gate skips dynamic concepts of non-HIGH
   rules on still chunks; static concepts come from the static cache, no SAM call);
3. track those concepts, each SAM prompt once for the camera, in parallel (the tracker uses the
   router's runtime segmenter, appends to the track store and publishes `tracks.updated`);
4. evaluate the camera's rules on a sliding window of stored tracks;
3. for each new candidate: chain + `candidate.new`; render its strip (P4-05, privacy-aware);
   **act first** if the policy says so (HIGH severity); verify with Tier 1; ask the policy;
   run the remaining actions; chain + publish every verdict and action.

The steps owned by other tasks (strip rendering, the safety policy, actions) are injected as
protocols, so the loop runs end to end with fakes until P4-05, P3-05 and P3-06 land.

**Pipeline:** hosted SAM takes 6 to 17 s per request while a camera delivers a chunk every 3 s,
so `run()` keeps up to `pipeline_depth` chunks per camera in flight. Each chunk is *submitted* in
arrival order: the lag check, idle sampling, motion gate and budget decide there whether and what
to segment, and the budget counts the chunks still in flight (`CameraSampler.reserve`). The SAM
concept pass (`ChunkTracker.segment`, stateless) then runs concurrently, and a per-camera reorder
buffer hands the results, strictly in chunk order, to everything stateful: identity hand-off and
track store appends (`ChunkTracker.stitch`), gaps, rule evaluation, candidates, verification,
actions and the evidence chain. A chunk that fails is counted and its slot freed; the chunks
behind it carry on. With `pipeline_depth=1` this is the sequential loop it replaced.

**Backpressure:** when a chunk is submitted more than `max_lag_s` after it ended, the loop
switches to keyframe mode for that camera and processes only one chunk in `keyframe_stride`
until it catches up. A camera whose pipeline is full waits for its oldest chunk to finish
before the next is submitted, so a backend that cannot keep up even with `pipeline_depth`
requests in flight shows up as lag and the keyframe policy, never as an unbounded queue of SAM
requests.

**Gaps:** a chunk (or a concept of a chunk) that is not segmented, for backpressure, idle
sampling, the budget or the motion gate, is a coverage gap, never an observed absence. A rule
window that overlaps a gap of one of the rule's concepts is cut to start after the gap. The rule
is evaluated on the cut window only if that still covers the history it needs (instantaneous
predicates need none; `persist`, `absence` and `dwell` their seconds; `rate` a minute; a
`sequence` the whole uncut window); otherwise it is not evaluated and counted in
`windows_blind`. So a duration rule cannot fail just because frames were skipped, and a HIGH
proximity rule is not blind for a whole window after an idle chunk. The lag, mode and cost
counters are exposed in `metrics` (and served at `/runtime/metrics`).

**Privacy:** the strip renderer receives the plan's `PrivacyMode` and the evaluation context
(P4-05, `runtime.strips`), so no person pixel leaves the loop in `anonymous` and `strict` mode.

A failing chunk (any `RulecamError`, e.g. a `ReplayMiss` or a model error) is logged, counted
and treated as a coverage gap; it never stops the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import Field

from rulecam.bus import Bus, Topic
from rulecam.contracts import (
    ActionRecord,
    Calibration,
    CandidateEvent,
    Chunk,
    Clock,
    Frozen,
    PrivacyMode,
    RuleIR,
    Track,
    TrackStore,
    Verifier,
    VerifierVerdict,
    Zone,
)
from rulecam.contracts.rule_ir import Absence, Dwell, Persist, Rate
from rulecam.contracts.rule_ir import Sequence as SequenceOp
from rulecam.errors import RulecamError
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.perception.errors import PerceptionError
from rulecam.perception.metrics import hosted_billed_frames
from rulecam.perception.motion_gate import MotionGate, MotionStats, is_still
from rulecam.perception.tracking import SegmentedChunk
from rulecam.rules.backtest import DecisionContext, candidate_payload
from rulecam.rules.engine import relevant_concepts
from rulecam.rules.ir import walk
from rulecam.runtime.sampling import CameraSampler, SamplingPolicy, has_high
from rulecam.runtime.sampling import chunk_end as chunk_end  # noqa: PLC0414 - re-export
from rulecam.verify.decision import Decision

_log = get_logger(__name__)

DEFAULT_WINDOW_S = 30.0
DEFAULT_MAX_LAG_S = 6.0
DEFAULT_KEYFRAME_STRIDE = 3
EPISODE_MERGE_S = 2.0
RATE_HISTORY_S = 60.0
DEFAULT_PIPELINE_DEPTH = 1
LATENCY_SAMPLES = 200
SEGMENT_THREADS = 32

StaticSink = Callable[[frozenset[str], str], None]
"""Told the static SAM prompts of a newly set plan, and the plan's version hash."""


class ChunkTracker(Protocol):
    """Tracks a chunk's concepts and stores the tracks (`perception.tracking.Tracker`)."""

    def segment(self, chunk: Chunk, concepts: Iterable[str]) -> SegmentedChunk:
        """Segment `concepts` in `chunk`; stateless, called for several chunks at once."""
        ...

    def stitch(self, chunk: Chunk, segmented: SegmentedChunk) -> list[Track]:
        """Hand identities off and store the tracks; called for a camera's chunks in order."""
        ...


class StripRenderer(Protocol):
    """Fills `strip_paths` with a 6-frame strip in the site's privacy mode (P4-05)."""

    def render(
        self,
        candidate: CandidateEvent,
        chunk: Chunk,
        mode: PrivacyMode,
        context: DecisionContext,
    ) -> CandidateEvent:
        """Return the candidate with its strip; in `anonymous`/`strict` mode, skeletons only.

        `context` carries the rule (whose subjects are drawn), its zones and the camera's
        calibration (the distance ruler). A renderer never raises: on failure the strip is empty.
        """
        ...


class Policy(Protocol):
    """The safety-asymmetric verification policy (P3-05)."""

    def act_first(self, candidate: CandidateEvent, rule: RuleIR) -> bool:
        """Whether actions run before verification (HIGH severity)."""
        ...

    def decide(self, candidate: CandidateEvent, rule: RuleIR, tier1: VerifierVerdict) -> Decision:
        """Decide what happens after Tier 1 (and Tier 2, which the policy may call)."""
        ...


class ActionRunner(Protocol):
    """Executes a rule's actions (P3-06)."""

    def run(
        self, candidate: CandidateEvent, rule: RuleIR, decision: Decision
    ) -> list[ActionRecord]:
        """Run the actions the decision allows and return their records."""
        ...


class SamUsage(Protocol):
    """Counts the runtime segmenter's SAM requests and billed frames (`perception.CallMeter`)."""

    def take_chunk(self, camera_id: str, chunk_id: str) -> tuple[int, int]:
        """Return `(requests, billed_frames)` made for one chunk, and forget them."""
        ...


class ActivePlan(Frozen):
    """What the loop enforces: the approved plan's rules and their geometry.

    `rule_cameras` maps a rule id to the cameras it runs on (derived at approval from the plan's
    feasibility evidence and the rule's zones, `runtime.activate`). A rule that is not listed
    runs on every camera.
    """

    rules: tuple[RuleIR, ...]
    privacy_mode: PrivacyMode = PrivacyMode.STRICT
    zones: dict[str, Zone] = Field(default_factory=dict)
    calibration: Calibration | None = None
    plan_version_hash: str = ""
    rule_cameras: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    def rules_on(self, camera_id: str) -> tuple[RuleIR, ...]:
        """The rules that run on `camera_id`."""
        return tuple(
            rule
            for rule in self.rules
            if rule.id not in self.rule_cameras or camera_id in self.rule_cameras[rule.id]
        )


class LoopMetrics(Frozen):
    """Runtime health and SAM cost, served to the UI.

    `chunks_skipped` counts backpressure skips; the other `chunks_skipped_*` counters say why a
    chunk was not segmented. `sam_calls` and `billed_frames` are the runtime's own SAM requests
    and their billed frames (hosted pricing), and `billed_frames_per_min` each camera's last
    minute, which the budget is checked against (together with in-flight reservations).
    `lag_s` is the largest of each camera's latest lag and `keyframe_mode` whether any camera is
    in it. `in_flight` counts chunks submitted for segmentation and not yet consumed, at most
    `pipeline_depth` per camera; the latency percentiles cover the last 200 segmentations.
    """

    chunks_processed: int = 0
    chunks_skipped: int = 0
    lag_s: float = 0.0
    keyframe_mode: bool = False
    candidates: int = 0
    windows_blind: int = 0
    chunks_failed: int = 0
    chunks_skipped_no_rule: int = 0
    chunks_skipped_idle: int = 0
    chunks_skipped_still: int = 0
    chunks_skipped_budget: int = 0
    sam_calls: int = 0
    billed_frames: int = 0
    billed_frames_per_min: dict[str, int] = Field(default_factory=dict)
    budget_exceeded: int = 0
    budget_degraded: tuple[str, ...] = ()
    in_flight: int = 0
    pipeline_depth: int = DEFAULT_PIPELINE_DEPTH
    segment_latency_p50_s: float = 0.0
    segment_latency_p95_s: float = 0.0


@dataclass
class _Lag:
    lag_s: float = 0.0
    keyframe_mode: bool = False
    stride_count: int = 0


@dataclass
class _State:
    processed: int = 0
    skipped: int = 0
    lag: dict[str, _Lag] = field(default_factory=dict)  # per camera
    candidates: int = 0
    windows_blind: int = 0
    failed: int = 0
    skipped_no_rule: int = 0
    skipped_idle: int = 0
    skipped_still: int = 0
    skipped_budget: int = 0
    sam_calls: int = 0
    billed_frames: int = 0
    last_end: dict[tuple[str, str, str], datetime] = field(default_factory=dict)
    gap_end: dict[tuple[str, str], datetime] = field(default_factory=dict)  # (camera, prompt)


@dataclass(frozen=True)
class _Job:
    """One chunk, with the decisions taken when it was submitted.

    `run` is what to segment (empty: the chunk is not segmented); `gap` the prompts it leaves
    unobserved. `rule_prompts` is the plan's prompt map as it was at submission.
    """

    chunk: Chunk
    plan: ActivePlan
    rules: tuple[RuleIR, ...]
    rule_prompts: Mapping[str, Mapping[str, bool]]
    prompts: Mapping[str, bool]
    gap: tuple[str, ...]
    run: tuple[str, ...] = ()
    motion_seen: bool = False


_Slot = tuple[_Job, "asyncio.Future[SegmentedChunk] | None"]


@dataclass
class _Lane:
    """One camera's pipeline: chunks waiting to be submitted, and submitted ones in order."""

    slots: asyncio.Semaphore
    intake: asyncio.Queue[Chunk | None] = field(default_factory=asyncio.Queue)
    ordered: asyncio.Queue[_Slot | None] = field(default_factory=asyncio.Queue)


def percentile(samples: Sequence[float], q: float) -> float:
    """Nearest-rank percentile of `samples` (0 when there are none).

    Args:
        samples: The values.
        q: The percentile, in (0, 100].

    Returns:
        The smallest sample with at least `q` percent of the samples at or below it.
    """
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def rule_prompts(rule: RuleIR, zones: Mapping[str, Zone]) -> dict[str, bool]:
    """The SAM prompts a rule needs, each mapped to whether it is static.

    A concept is segmented by its `ConceptSpec.prompt` only: the engine matches tracks by a
    concept's key or prompt, so segmenting the key too would double the calls. A name the rule
    reads without a spec (e.g. a state such as "open door") is its own prompt.

    Args:
        rule: The rule.
        zones: The plan's zones (a buffer zone needs its anchor concept).

    Returns:
        Prompt -> static, in sorted prompt order.
    """
    names = relevant_concepts(rule, zones)
    prompts: dict[str, bool] = {}
    known: set[str] = set()
    for key, spec in rule.concepts.items():
        known |= {key, spec.prompt}
        if key in names or spec.prompt in names:
            prompts[spec.prompt] = prompts.get(spec.prompt, True) and spec.static
    for name in names - known:
        prompts[name] = False
    return dict(sorted(prompts.items()))


def merge_prompts(per_rule: Iterable[Mapping[str, bool]]) -> dict[str, bool]:
    """Union of rule prompts; a prompt is static only if every rule using it says so."""
    merged: dict[str, bool] = {}
    for prompts in per_rule:
        for prompt, static in prompts.items():
            merged[prompt] = merged.get(prompt, True) and static
    return merged


def history_s(rule: RuleIR) -> float | None:
    """Seconds of uninterrupted observation the rule's predicate needs to be judged.

    Returns:
        0 for an instantaneous predicate, the longest `persist`/`absence`/`dwell` duration (a
        minute for `rate`), or `None` for a `sequence`, which needs the whole window.
    """
    need = 0.0
    for _, node in walk(rule.predicate):
        if isinstance(node, SequenceOp):
            return None
        if isinstance(node, Persist | Absence):
            need = max(need, node.seconds)
        elif isinstance(node, Dwell):
            need = max(need, node.total_s_gte)
        elif isinstance(node, Rate):
            need = max(need, RATE_HISTORY_S)
    return need


def plan_concepts(plan: ActivePlan) -> list[str]:
    """Return every concept the plan's rules need, in first-seen order."""
    seen: dict[str, None] = {}
    for rule in plan.rules:
        for concept in sorted(relevant_concepts(rule, plan.zones)):
            seen.setdefault(concept, None)
    return list(seen)


class RuntimeLoop:
    """Wires perception, rules, verification, policy, actions and evidence together."""

    def __init__(
        self,
        *,
        bus: Bus,
        clock: Clock,
        tracker: ChunkTracker,
        store: TrackStore,
        verifier: Verifier,
        policy: Policy,
        actions: ActionRunner,
        strips: StripRenderer,
        chain: EventChain,
        window_s: float = DEFAULT_WINDOW_S,
        max_lag_s: float = DEFAULT_MAX_LAG_S,
        keyframe_stride: int = DEFAULT_KEYFRAME_STRIDE,
        sampling: SamplingPolicy | None = None,
        motion_gate: MotionGate | None = None,
        usage: SamUsage | None = None,
        static_sink: StaticSink | None = None,
        companions: Sequence[Callable[[], Awaitable[None]]] = (),
        pipeline_depth: int = DEFAULT_PIPELINE_DEPTH,
    ) -> None:
        """Create the loop; it does nothing until a plan is set.

        Args:
            bus: The event bus (chunks in, everything else out).
            clock: Time source for the lag measurement.
            tracker: Tracks concepts per chunk and appends to `store`.
            store: Track store queried for each rule window.
            verifier: Tier 1 verifier.
            policy: Safety-asymmetric policy.
            actions: Action runner.
            strips: Strip renderer.
            chain: Hash-chained evidence log.
            window_s: Sliding window evaluated per chunk.
            max_lag_s: Lag above which keyframe mode starts.
            keyframe_stride: In keyframe mode, process one chunk in this many.
            sampling: Idle sampling and SAM budget (default: every chunk, no budget).
            motion_gate: Measures motion and gates dynamic concepts on still chunks; `None`
                treats every chunk as moving (nothing gated, HIGH rules never thinned).
            usage: SAM request and billed-frame counters of the tracker's segmenter; without
                it the cost counters and the budget stay at zero.
            static_sink: Told each new plan's static prompts (the static cache's concepts).
            companions: Background coroutines `run()` keeps alive alongside the chunk loop
                (e.g. the static cache's `coverage.changed` listener).
            pipeline_depth: Chunks per camera `run()` keeps in flight (segmenting, or waiting
                for an earlier chunk to be consumed); 1 processes one chunk at a time.
        """
        self._bus = bus
        self._clock = clock
        self._tracker = tracker
        self._store = store
        self._verifier = verifier
        self._policy = policy
        self._actions = actions
        self._strips = strips
        self._chain = chain
        self._window = timedelta(seconds=window_s)
        self._max_lag_s = max_lag_s
        self._stride = max(1, keyframe_stride)
        self._sampler = CameraSampler(sampling)
        self._gate = motion_gate
        self._usage = usage
        self._static_sink = static_sink
        self._companions = tuple(companions)
        self._depth = max(1, pipeline_depth)
        self._plan: ActivePlan | None = None
        self._prompts: dict[str, dict[str, bool]] = {}
        self._state = _State()
        self._lock = threading.RLock()  # submission and in-order consumption share the state
        self._in_flight = 0  # touched on the event loop only
        self._latencies: deque[float] = deque(maxlen=LATENCY_SAMPLES)

    @property
    def plan(self) -> ActivePlan | None:
        """The plan being enforced, if any."""
        return self._plan

    @property
    def metrics(self) -> LoopMetrics:
        """Current runtime health."""
        s = self._state
        lags = list(s.lag.values())
        latencies = list(self._latencies)
        return LoopMetrics(
            chunks_processed=s.processed,
            chunks_skipped=s.skipped,
            lag_s=round(max((lag.lag_s for lag in lags), default=0.0), 3),
            keyframe_mode=any(lag.keyframe_mode for lag in lags),
            candidates=s.candidates,
            windows_blind=s.windows_blind,
            chunks_failed=s.failed,
            chunks_skipped_no_rule=s.skipped_no_rule,
            chunks_skipped_idle=s.skipped_idle,
            chunks_skipped_still=s.skipped_still,
            chunks_skipped_budget=s.skipped_budget,
            sam_calls=s.sam_calls,
            billed_frames=s.billed_frames,
            billed_frames_per_min=self._sampler.billed_per_minute(),
            budget_exceeded=self._sampler.budget_exceeded,
            budget_degraded=tuple(self._sampler.degraded()),
            in_flight=self._in_flight,
            pipeline_depth=self._depth,
            segment_latency_p50_s=round(percentile(latencies, 50), 3),
            segment_latency_p95_s=round(percentile(latencies, 95), 3),
        )

    def set_plan(self, plan: ActivePlan) -> None:
        """Start enforcing `plan` (replaces any previous one).

        Args:
            plan: The approved plan.
        """
        self._prompts = {rule.id: rule_prompts(rule, plan.zones) for rule in plan.rules}
        self._plan = plan
        unscoped = [rule.id for rule in plan.rules if rule.id not in plan.rule_cameras]
        if unscoped:
            _log.warning("runtime_rules_unscoped", rules=unscoped, runs_on="every camera")
        if self._static_sink is not None:
            merged = merge_prompts(self._prompts.values())
            static = frozenset(p for p, is_static in merged.items() if is_static)
            self._static_sink(static, plan.plan_version_hash)
        self._chain.append(
            "plan",
            {"plan_version_hash": plan.plan_version_hash, "rules": [r.id for r in plan.rules]},
        )

    async def run(self) -> None:
        """Process `chunk.ready` messages until cancelled (blocking work runs in threads).

        Up to `pipeline_depth` chunks per camera are segmented at once; their results are
        consumed in chunk order (module docstring). A chunk that fails with a `RulecamError` is
        logged and counted in `chunks_failed`; the loop carries on with the next chunk.
        Companion coroutines run until the loop stops.
        """
        companions = [asyncio.create_task(_await(start)) for start in self._companions]
        try:
            await self._consume()
        finally:
            for task in companions:
                task.cancel()

    async def _consume(self) -> None:
        executor = ThreadPoolExecutor(SEGMENT_THREADS, thread_name_prefix="rulecam-segment")
        lanes: dict[str, _Lane] = {}
        try:
            async with asyncio.TaskGroup() as group:
                async with contextlib.aclosing(self._bus.subscribe(Topic.CHUNK_READY)) as messages:
                    async for message in messages:
                        chunk = message.payload
                        if not isinstance(chunk, Chunk):
                            continue
                        lane = lanes.get(chunk.camera_id)
                        if lane is None:
                            lane = lanes[chunk.camera_id] = _Lane(asyncio.Semaphore(self._depth))
                            group.create_task(self._submit(lane, executor))
                            group.create_task(self._drain(lane))
                        lane.intake.put_nowait(chunk)
                for lane in lanes.values():
                    lane.intake.put_nowait(None)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    async def _submit(self, lane: _Lane, executor: ThreadPoolExecutor) -> None:
        """Submit a camera's chunks in arrival order, at most `pipeline_depth` in flight."""
        loop = asyncio.get_running_loop()
        while (chunk := await lane.intake.get()) is not None:
            await lane.slots.acquire()  # a full pipeline waits here, so the lag shows it
            try:
                job = await asyncio.to_thread(self._admit, chunk)
            except BaseException:
                lane.slots.release()
                raise
            future = loop.run_in_executor(executor, self._segment, job) if job and job.run else None
            if future is None:
                lane.slots.release()
            else:
                self._in_flight += 1
            if job is not None:
                lane.ordered.put_nowait((job, future))
        lane.ordered.put_nowait(None)

    async def _drain(self, lane: _Lane) -> None:
        """Consume a camera's submitted chunks strictly in submission order."""
        while (slot := await lane.ordered.get()) is not None:
            job, future = slot
            try:
                outcome: SegmentedChunk | Exception | None = None
                if future is not None:
                    try:
                        outcome = await future
                    except Exception as exc:
                        outcome = exc
                await asyncio.to_thread(self._finish, job, outcome)
            except RulecamError:
                self._state.failed += 1
                _log.exception(
                    "runtime_chunk_failed",
                    chunk_id=job.chunk.chunk_id,
                    camera_id=job.chunk.camera_id,
                )
            finally:
                if future is not None:
                    self._in_flight -= 1
                    lane.slots.release()

    def _should_process(self, chunk: Chunk) -> bool:
        lag = self._state.lag.setdefault(chunk.camera_id, _Lag())
        lag.lag_s = (self._clock.now() - chunk_end(chunk)).total_seconds()
        lagging = lag.lag_s > self._max_lag_s
        if lagging != lag.keyframe_mode:
            _log.warning(
                "runtime_keyframe_mode",
                on=lagging,
                lag_s=round(lag.lag_s, 2),
                camera_id=chunk.camera_id,
            )
            lag.keyframe_mode = lagging
            lag.stride_count = 0
        if not lag.keyframe_mode:
            return True
        lag.stride_count += 1
        return (lag.stride_count - 1) % self._stride == 0

    def handle_chunk(self, chunk: Chunk) -> list[CandidateEvent]:
        """Run the whole pipeline for one chunk, synchronously (submit, segment, consume).

        Bus messages reach subscribers safely from a worker thread because the API binds the
        bus to its event loop (`Bus.bind_loop`).

        Args:
            chunk: The chunk that just became ready.

        Returns:
            The new candidates found (after de-duplication across windows).

        Raises:
            RulecamError: The chunk failed (it is accounted for as a gap first).
        """
        job = self._admit(chunk)
        if job is None:
            return []
        if not job.run:
            return self._finish(job, None)
        try:
            outcome: SegmentedChunk | Exception = self._segment(job)
        except Exception as exc:
            outcome = exc
        return self._finish(job, outcome)

    def _admit(self, chunk: Chunk) -> _Job | None:
        """Submission: decide whether and what to segment (lag, sampling, budget, motion gate).

        Returns:
            The job to consume in order, or `None` when no plan or rule covers the camera.
        """
        plan, rule_prompts = self._plan, self._prompts
        if plan is None:
            return None
        rules = plan.rules_on(chunk.camera_id)
        if not rules:
            with self._lock:
                self._state.skipped_no_rule += 1
            return None
        prompts = merge_prompts(rule_prompts[rule.id] for rule in rules)
        job = _Job(chunk, plan, rules, rule_prompts, prompts, gap=tuple(prompts))
        with self._lock:
            if not self._should_process(chunk):
                self._state.skipped += 1
                return job
        stats = self._measure(chunk)
        moving = stats is None or self._gate is None or not is_still(stats, self._gate.config)
        with self._lock:
            admission = self._sampler.admit(chunk, rules, moving=moving)
            if admission != "run":
                if admission == "idle":
                    self._state.skipped_idle += 1
                else:
                    self._state.skipped_budget += 1
                return job
            run = self._select(chunk, rules, prompts, stats)
            gap = tuple(p for p in prompts if p not in run)
            dynamic = [p for p in prompts if not prompts[p]]
            if dynamic and not any(p in run for p in dynamic):
                # Every dynamic concept was gated on a still chunk: nothing to segment.
                self._state.skipped_still += 1
                return replace(job, gap=gap)
            if self._usage is not None:
                calls = sum(1 for p in run if not prompts[p])  # static prompts are cached
                self._sampler.reserve(chunk, calls * hosted_billed_frames(chunk.n_frames))
            return replace(job, gap=gap, run=tuple(run), motion_seen=stats is not None and moving)

    def _segment(self, job: _Job) -> SegmentedChunk:
        """The concurrent step: the SAM concept pass of a submitted chunk."""
        started = self._clock.now()
        segmented = self._tracker.segment(job.chunk, job.run)
        self._latencies.append((self._clock.now() - started).total_seconds())
        return segmented

    def _finish(
        self, job: _Job, outcome: SegmentedChunk | Exception | None
    ) -> list[CandidateEvent]:
        """The in-order step: gaps, hand-off, rules and candidates of one submitted chunk.

        Args:
            job: The submitted chunk.
            outcome: Its segmentation, the error it raised, or `None` if it was not segmented.

        Returns:
            The new candidates.
        """
        chunk = job.chunk
        if not job.run:
            with self._lock:
                self._mark_gap(chunk, job.gap)
            return []
        try:
            tracks = self._stitch(chunk, outcome)
        except Exception:
            with self._lock:
                # Nothing of a failed chunk was observed: a gap, like any other skipped chunk.
                self._state.processed += 1
                self._mark_gap(chunk, job.prompts)
                self._sampler.record(chunk, billed_frames=self._bill(chunk), active=False)
            raise
        with self._lock:
            return self._process(job, tracks)

    def _stitch(self, chunk: Chunk, outcome: SegmentedChunk | Exception | None) -> list[Track]:
        """Hand off a segmented chunk's identities, or re-raise the error its segment raised."""
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            msg = "a segmented chunk needs its segmentation"
            raise ValueError(msg)
        return self._tracker.stitch(chunk, outcome)

    def _bill(self, chunk: Chunk) -> int:
        """Count the chunk's SAM requests and billed frames; return the billed frames."""
        if self._usage is None:
            return 0
        calls, billed = self._usage.take_chunk(chunk.camera_id, chunk.chunk_id)
        self._state.sam_calls += calls
        self._state.billed_frames += billed
        return billed

    def _measure(self, chunk: Chunk) -> MotionStats | None:
        if self._gate is None:
            return None
        try:
            return self._gate.measure(chunk)
        except PerceptionError as exc:
            _log.warning("runtime_motion_unmeasured", chunk_id=chunk.chunk_id, error=str(exc))
            return None

    def _select(
        self,
        chunk: Chunk,
        rules: Sequence[RuleIR],
        prompts: Mapping[str, bool],
        stats: MotionStats | None,
    ) -> list[str]:
        """The prompts to segment: static ones always (cached), dynamic ones the gate lets run."""
        if self._gate is None:
            return list(prompts)
        skipped_keys = set(self._gate.gate(chunk, rules, stats).skipped)
        gated: set[str] = set()
        for prompt in prompts:
            keys = {k for r in rules for k, spec in r.concepts.items() if spec.prompt == prompt}
            if keys and keys <= skipped_keys and not prompts[prompt]:
                gated.add(prompt)
        return [p for p in prompts if p not in gated]

    def _process(self, job: _Job, tracks: Sequence[Track]) -> list[CandidateEvent]:
        """Evaluate the camera's rules after a segmented chunk and handle new candidates."""
        chunk, rules = job.chunk, job.rules
        self._state.processed += 1
        self._mark_gap(chunk, job.gap)
        billed = self._bill(chunk)
        found = [pair for rule in rules for pair in self._evaluate(rule, job)]
        for candidate, context in found:
            self._handle_candidate(candidate, context, chunk, job.plan.privacy_mode)
        dynamic_seen = any(not job.prompts.get(t.concept, False) for t in tracks)
        active = bool(found) or dynamic_seen or (has_high(rules) and job.motion_seen)
        self._sampler.record(chunk, billed_frames=billed, active=active)
        return [candidate for candidate, _ in found]

    def _mark_gap(self, chunk: Chunk, prompts: Iterable[str]) -> None:
        """Record that `prompts` were not observed on the chunk's camera until its end."""
        end = chunk_end(chunk)
        for prompt in prompts:
            key = (chunk.camera_id, prompt)
            previous = self._state.gap_end.get(key)
            self._state.gap_end[key] = end if previous is None else max(previous, end)

    def _evaluate(self, rule: RuleIR, job: _Job) -> list[tuple[CandidateEvent, DecisionContext]]:
        chunk, plan = job.chunk, job.plan
        t1 = chunk_end(chunk)
        t0 = t1 - self._window
        self._forget_before(chunk.camera_id, t0)
        gaps = [self._state.gap_end.get((chunk.camera_id, p)) for p in job.rule_prompts[rule.id]]
        gap_end = max((g for g in gaps if g is not None), default=None)
        if gap_end is not None and gap_end > t0:
            # Some of this window was not observed: judge only what came after the gap, and
            # only if that covers the history the rule needs. Never blind means compliant.
            need = history_s(rule)
            span_s = (t1 - gap_end).total_seconds()
            if need is None or span_s <= 0 or span_s < need:
                self._state.windows_blind += 1
                return []
            t0 = gap_end
        context = DecisionContext(
            rule=rule,
            calibration=plan.calibration,
            zones=plan.zones,
            t0=t0,
            t1=t1,
            plan_version_hash=plan.plan_version_hash,
        )
        tracks = self._store.window(chunk.camera_id, t0, t1)
        return [(c, context) for c in context.evaluate(tracks) if self._is_new(c)]

    def _forget_before(self, camera_id: str, t0: datetime) -> None:
        """Drop de-duplication entries that can no longer merge with a window starting at `t0`."""
        horizon = t0 - timedelta(seconds=EPISODE_MERGE_S)
        last_end = self._state.last_end
        for key in [k for k, end in last_end.items() if k[1] == camera_id and end < horizon]:
            del last_end[key]

    def _is_new(self, candidate: CandidateEvent) -> bool:
        """Drop episodes already reported by an earlier, overlapping window."""
        key = (candidate.rule_id, candidate.camera_id, str(candidate.predicate_trace["subject"]))
        last = self._state.last_end.get(key)
        if last is not None and candidate.ts_start <= last + timedelta(seconds=EPISODE_MERGE_S):
            self._state.last_end[key] = max(last, candidate.ts_end)
            return False
        self._state.last_end[key] = candidate.ts_end
        return True

    def _handle_candidate(
        self,
        candidate: CandidateEvent,
        context: DecisionContext,
        chunk: Chunk,
        mode: PrivacyMode,
    ) -> Decision:
        """Strip, verify, decide and act on one candidate; return the policy's decision."""
        rule = context.rule
        self._state.candidates += 1
        # The context lets `rulecam audit` re-derive this decision from the track store (E9).
        self._chain.append("candidate", candidate_payload(candidate, context))
        self._bus.publish(Topic.CANDIDATE_NEW, candidate)
        candidate = self._strips.render(candidate, chunk, mode, context)
        act_first = self._policy.act_first(candidate, rule)
        if act_first:
            immediate = Decision(alert=True, route="auto", act_first=True, reason="act first")
            self._record_actions(candidate, self._actions.run(candidate, rule, immediate))
        tier1 = self._verifier.verify(candidate, rule)
        self._chain.append("verdict", {"event_id": candidate.event_id, **_dump(tier1)})
        self._bus.publish(Topic.VERDICT_NEW, VerdictNew(event_id=candidate.event_id, verdict=tier1))
        decision = self._policy.decide(candidate, rule, tier1)
        if not act_first:
            self._record_actions(candidate, self._actions.run(candidate, rule, decision))
            return decision
        # Actions already ran. Chain the after-the-fact decision, and if a verifier demoted the
        # alert, ask a person to review it (notify/escalate only: nothing is re-cut or re-spoken).
        self._chain.append("verdict", {"event_id": candidate.event_id, "decision": _dump(decision)})
        if decision.route == "human_queue":
            self._record_actions(candidate, self._actions.run(candidate, rule, decision))
        return decision

    def _record_actions(self, candidate: CandidateEvent, records: Sequence[ActionRecord]) -> None:
        for record in records:
            self._chain.append("action", {"event_id": candidate.event_id, **_dump(record)})
            self._bus.publish(
                Topic.ACTION_DONE, ActionDone(event_id=candidate.event_id, action=record)
            )


class VerdictNew(Frozen):
    """Payload of `verdict.new`."""

    event_id: str
    verdict: VerifierVerdict


class ActionDone(Frozen):
    """Payload of `action.done`."""

    event_id: str
    action: ActionRecord


async def _await(start: Callable[[], Awaitable[None]]) -> None:
    await start()


def _dump(model: Frozen) -> Mapping[str, object]:
    return model.model_dump(mode="json")
