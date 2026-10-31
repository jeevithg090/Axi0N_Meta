"""The runtime orchestrator (task P3-08): one chunk in, chained and published decisions out.

For every `chunk.ready`:

1. track the active plan's concepts (the tracker uses the router's runtime segmenter, appends to
   the track store and publishes `tracks.updated`);
2. evaluate every active rule on a sliding window of stored tracks;
3. for each new candidate: chain + `candidate.new`; render its strip (P4-05, privacy-aware);
   **act first** if the policy says so (HIGH severity); verify with Tier 1; ask the policy;
   run the remaining actions; chain + publish every verdict and action.

The steps owned by other tasks (strip rendering, the safety policy, actions) are injected as
protocols, so the loop runs end to end with fakes until P4-05, P3-05 and P3-06 land.

**Backpressure:** when a chunk arrives more than `max_lag_s` after it ended, the loop switches to
keyframe mode and processes only one chunk in `keyframe_stride` until it catches up. A skipped
chunk is a coverage gap, never an observed absence: a rule window that overlaps a gap is not
evaluated (it is counted in `windows_blind`), so a duration rule cannot fail just because frames
were dropped. The lag and mode are exposed in `metrics` (and served at `/runtime/metrics`).

**Privacy:** the strip renderer receives the plan's `PrivacyMode` (P4-05 `render_strip(event,
mode)`), so only skeleton renders leave the loop in `anonymous` and `strict` mode.

A failing chunk (any `RulecamError`, e.g. a `ReplayMiss` or a model error) is logged and skipped;
it never stops the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable, Mapping, Sequence
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
from rulecam.errors import RulecamError
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.rules.engine import evaluate, relevant_concepts
from rulecam.verify.decision import Decision

_log = get_logger(__name__)

DEFAULT_WINDOW_S = 30.0
DEFAULT_MAX_LAG_S = 6.0
DEFAULT_KEYFRAME_STRIDE = 3
EPISODE_MERGE_S = 2.0


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


class ActivePlan(Frozen):
    """What the loop enforces: the approved plan's rules and their geometry."""

    rules: tuple[RuleIR, ...]
    privacy_mode: PrivacyMode = PrivacyMode.STRICT
    zones: dict[str, Zone] = Field(default_factory=dict)
    calibration: Calibration | None = None
    plan_version_hash: str = ""


class LoopMetrics(Frozen):
    """Runtime health, served to the UI."""

    chunks_processed: int = 0
    chunks_skipped: int = 0
    lag_s: float = 0.0
    keyframe_mode: bool = False
    candidates: int = 0
    windows_blind: int = 0
    chunks_failed: int = 0


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
    last_end: dict[tuple[str, str, str], datetime] = field(default_factory=dict)
    gap_end: dict[str, datetime] = field(default_factory=dict)


def chunk_end(chunk: Chunk) -> datetime:
    """Return when a chunk's last frame ends."""
    return chunk.start_ts + timedelta(seconds=chunk.n_frames / chunk.fps)


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
        self._plan: ActivePlan | None = None
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
        )

    def set_plan(self, plan: ActivePlan) -> None:
        """Start enforcing `plan` (replaces any previous one).

        Args:
            plan: The approved plan.
        """
        self._plan = plan
        self._chain.append(
            "plan",
            {"plan_version_hash": plan.plan_version_hash, "rules": [r.id for r in plan.rules]},
        )

    async def run(self) -> None:
        """Process `chunk.ready` messages until cancelled (blocking work runs in a thread).

        A chunk that fails with a `RulecamError` is logged and counted in `chunks_failed`; the
        loop carries on with the next chunk.
        """
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
        if not self._should_process(chunk):
            self._state.skipped += 1
            self._state.gap_end[chunk.camera_id] = chunk_end(chunk)
            return []
        self._state.processed += 1
        self._tracker.process_chunk(chunk, plan_concepts(plan))
        new = [c for rule in plan.rules for c in self._evaluate(rule, plan, chunk)]
        for candidate in new:
            self._handle_candidate(
                candidate, self._rule(plan, candidate.rule_id), chunk, plan.privacy_mode
            )
        return new

    @staticmethod
    def _rule(plan: ActivePlan, rule_id: str) -> RuleIR:
        return next(r for r in plan.rules if r.id == rule_id)

    def _evaluate(self, rule: RuleIR, plan: ActivePlan, chunk: Chunk) -> list[CandidateEvent]:
        t1 = chunk_end(chunk)
        t0 = t1 - self._window
        self._forget_before(chunk.camera_id, t0)
        gap_end = self._state.gap_end.get(chunk.camera_id)
        if gap_end is not None and gap_end > t0:
            # Frames in this window were skipped under backpressure: blind, not compliant.
            self._state.windows_blind += 1
            return []
        tracks = self._store.window(chunk.camera_id, t0, t1)
        candidates = evaluate(
            rule,
            tracks,
            calibration=plan.calibration,
            zones=plan.zones,
            t0=t0,
            t1=t1,
            plan_version_hash=plan.plan_version_hash,
        )
        return [c for c in candidates if self._is_new(c)]

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
        self, candidate: CandidateEvent, rule: RuleIR, chunk: Chunk, mode: PrivacyMode
    ) -> Decision:
        """Strip, verify, decide and act on one candidate; return the policy's decision."""
        self._state.candidates += 1
        self._chain.append("candidate", candidate)
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


def _dump(model: Frozen) -> Mapping[str, object]:
    return model.model_dump(mode="json")
