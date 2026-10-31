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

**Backpressure:** when a chunk arrives more than `max_lag_s` after it ended, the loop switches to
keyframe mode and processes only one chunk in `keyframe_stride` until it catches up.

**Gaps:** a chunk (or a concept of a chunk) that is not segmented, for backpressure, idle
sampling, the budget or the motion gate, is a coverage gap, never an observed absence. A rule
window that overlaps a gap of one of the rule's concepts is cut to start after the gap. The rule
is evaluated on the cut window only if that still covers the history it needs (instantaneous
predicates need none; `persist`, `absence` and `dwell` their seconds; `rate` a minute; a
`sequence` the whole uncut window); otherwise it is not evaluated and counted in
`windows_blind`. So a duration rule cannot fail just because frames were skipped, and a HIGH
proximity rule is not blind for a whole window after an idle chunk. The lag, mode and cost
counters are exposed in `metrics` (and served at `/runtime/metrics`).

**Privacy:** the strip renderer receives the plan's `PrivacyMode` (P4-05 `render_strip(event,
mode)`), so only skeleton renders leave the loop in `anonymous` and `strict` mode.

A failing chunk (any `RulecamError`, e.g. a `ReplayMiss` or a model error) is logged and skipped;
it never stops the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
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
from rulecam.perception.motion_gate import MotionGate, MotionStats, is_still
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

StaticSink = Callable[[frozenset[str], str], None]
"""Told the static SAM prompts of a newly set plan, and the plan's version hash."""


class ChunkTracker(Protocol):
    """Tracks a chunk's concepts and stores the tracks (`perception.tracking.Tracker`)."""

    def process_chunk(self, chunk: Chunk, concepts: Iterable[str]) -> list[Track]:
        """Track `concepts` in `chunk`."""
        ...


class StripRenderer(Protocol):
    """Fills `strip_paths` with a 6-frame strip in the site's privacy mode (P4-05)."""

    def render(self, candidate: CandidateEvent, chunk: Chunk, mode: PrivacyMode) -> CandidateEvent:
        """Return the candidate with its strip; in `anonymous`/`strict` mode, skeletons only."""
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

    def totals(self, camera_id: str) -> tuple[int, int]:
        """Return `(requests, billed_frames)` so far for `camera_id`."""
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
    minute, which the budget is checked against.
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


@dataclass
class _State:
    processed: int = 0
    skipped: int = 0
    lag_s: float = 0.0
    keyframe_mode: bool = False
    stride_count: int = 0
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
        self._plan: ActivePlan | None = None
        self._prompts: dict[str, dict[str, bool]] = {}
        self._state = _State()

    @property
    def plan(self) -> ActivePlan | None:
        """The plan being enforced, if any."""
        return self._plan

    @property
    def metrics(self) -> LoopMetrics:
        """Current runtime health."""
        s = self._state
        return LoopMetrics(
            chunks_processed=s.processed,
            chunks_skipped=s.skipped,
            lag_s=round(s.lag_s, 3),
            keyframe_mode=s.keyframe_mode,
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
        """Process `chunk.ready` messages until cancelled (blocking work runs in a thread).

        A chunk that fails with a `RulecamError` is logged and counted in `chunks_failed`; the
        loop carries on with the next chunk. Companion coroutines run until the loop stops.
        """
        companions = [asyncio.create_task(_await(start)) for start in self._companions]
        try:
            await self._consume()
        finally:
            for task in companions:
                task.cancel()

    async def _consume(self) -> None:
        async with contextlib.aclosing(self._bus.subscribe(Topic.CHUNK_READY)) as messages:
            async for message in messages:
                chunk = message.payload
                if not isinstance(chunk, Chunk):
                    continue
                try:
                    await asyncio.to_thread(self.handle_chunk, chunk)
                except RulecamError:
                    self._state.failed += 1
                    _log.exception(
                        "runtime_chunk_failed", chunk_id=chunk.chunk_id, camera_id=chunk.camera_id
                    )

    def _should_process(self, chunk: Chunk) -> bool:
        s = self._state
        s.lag_s = (self._clock.now() - chunk_end(chunk)).total_seconds()
        lagging = s.lag_s > self._max_lag_s
        if lagging != s.keyframe_mode:
            _log.warning("runtime_keyframe_mode", on=lagging, lag_s=round(s.lag_s, 2))
            s.keyframe_mode = lagging
            s.stride_count = 0
        if not s.keyframe_mode:
            return True
        s.stride_count += 1
        return (s.stride_count - 1) % self._stride == 0

    def handle_chunk(self, chunk: Chunk) -> list[CandidateEvent]:
        """Run the whole pipeline for one chunk.

        `run()` calls this in a worker thread; bus messages reach subscribers safely because
        the API binds the bus to its event loop (`Bus.bind_loop`).

        Args:
            chunk: The chunk that just became ready.

        Returns:
            The new candidates found (after de-duplication across windows).
        """
        plan = self._plan
        if plan is None:
            return []
        rules = plan.rules_on(chunk.camera_id)
        if not rules:
            self._state.skipped_no_rule += 1
            return []
        prompts = merge_prompts(self._prompts[rule.id] for rule in rules)
        if not self._should_process(chunk):
            self._state.skipped += 1
            self._mark_gap(chunk, prompts)
            return []
        stats = self._measure(chunk)
        moving = stats is None or self._gate is None or not is_still(stats, self._gate.config)
        admission = self._sampler.admit(chunk, rules, moving=moving)
        if admission != "run":
            if admission == "idle":
                self._state.skipped_idle += 1
            else:
                self._state.skipped_budget += 1
            self._mark_gap(chunk, prompts)
            return []
        run = self._select(chunk, rules, prompts, stats)
        self._mark_gap(chunk, [p for p in prompts if p not in run])
        dynamic = [p for p in prompts if not prompts[p]]
        if dynamic and not any(p in run for p in dynamic):
            # Every dynamic concept was gated on a still chunk: nothing to segment.
            self._state.skipped_still += 1
            return []
        return self._process(chunk, plan, rules, run, motion_seen=stats is not None and moving)

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

    def _process(
        self,
        chunk: Chunk,
        plan: ActivePlan,
        rules: Sequence[RuleIR],
        run: Sequence[str],
        *,
        motion_seen: bool,
    ) -> list[CandidateEvent]:
        """Segment `run` on the chunk, evaluate the camera's rules and handle new candidates."""
        self._state.processed += 1
        before = self._usage.totals(chunk.camera_id) if self._usage is not None else (0, 0)
        tracks = self._tracker.process_chunk(chunk, run)
        after = self._usage.totals(chunk.camera_id) if self._usage is not None else (0, 0)
        calls, billed = after[0] - before[0], after[1] - before[1]
        self._state.sam_calls += calls
        self._state.billed_frames += billed
        found = [pair for rule in rules for pair in self._evaluate(rule, plan, chunk)]
        for candidate, context in found:
            self._handle_candidate(candidate, context, chunk, plan.privacy_mode)
        prompts = merge_prompts(self._prompts[rule.id] for rule in rules)
        dynamic_seen = any(not prompts.get(t.concept, False) for t in tracks)
        active = bool(found) or dynamic_seen or (has_high(rules) and motion_seen)
        self._sampler.record(chunk, billed_frames=billed, active=active)
        return [candidate for candidate, _ in found]

    def _mark_gap(self, chunk: Chunk, prompts: Iterable[str]) -> None:
        """Record that `prompts` were not observed on the chunk's camera until its end."""
        end = chunk_end(chunk)
        for prompt in prompts:
            key = (chunk.camera_id, prompt)
            previous = self._state.gap_end.get(key)
            self._state.gap_end[key] = end if previous is None else max(previous, end)

    def _evaluate(
        self, rule: RuleIR, plan: ActivePlan, chunk: Chunk
    ) -> list[tuple[CandidateEvent, DecisionContext]]:
        t1 = chunk_end(chunk)
        t0 = t1 - self._window
        self._forget_before(chunk.camera_id, t0)
        gaps = [self._state.gap_end.get((chunk.camera_id, p)) for p in self._prompts[rule.id]]
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
        candidate = self._strips.render(candidate, chunk, mode)
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
