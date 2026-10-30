"""Backtesting rules over stored tracks, and re-deriving recorded decisions (P1-07, MP §3.4, E9).

Neither function calls SAM or any model: both re-run the pure engine on tracks from a
`TrackStore`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import Field, ValidationError

from rulecam.contracts import (
    Calibration,
    CandidateEvent,
    EventRecord,
    Frozen,
    RuleIR,
    Track,
    TrackStore,
    Zone,
)
from rulecam.contracts.rule_ir import StateCheck
from rulecam.rules.engine import EPISODE_GAP_S, evaluate
from rulecam.rules.errors import RuleEvaluationError
from rulecam.rules.hashing import rule_ir_hash
from rulecam.rules.ir import parse_ref, referenced_concepts, referenced_zones, state_concepts, walk
from rulecam.rules.pose import PoseFeatures
from rulecam.rules.scene import resolve_zone

Backtestable = Literal["yes", "partial", "no"]
_SECONDS_PER_HOUR = 3600.0
_TIMES = "\N{MULTIPLICATION SIGN}"


class DecisionContext(Frozen):
    """Everything besides the tracks that an evaluation depended on.

    Stored next to each candidate in its chain record, so the decision can be re-derived from the
    track store alone (`rederive`).
    """

    rule: RuleIR
    calibration: Calibration | None = None
    zones: dict[str, Zone] = Field(default_factory=dict)
    t0: datetime
    t1: datetime
    hz: float = 10.0
    episode_gap_s: float = EPISODE_GAP_S
    cooldown_s: float = 0.0
    plan_version_hash: str = ""

    def evaluate(
        self, tracks: Sequence[Track], *, pose: PoseFeatures | None = None
    ) -> list[CandidateEvent]:
        """Run the engine on `tracks` with this context."""
        return evaluate(
            self.rule,
            tracks,
            calibration=self.calibration,
            zones=self.zones,
            t0=self.t0,
            t1=self.t1,
            hz=self.hz,
            episode_gap_s=self.episode_gap_s,
            cooldown_s=self.cooldown_s,
            plan_version_hash=self.plan_version_hash,
            pose=pose,
        )


class BacktestDiff(Frozen):
    """Events a new rule version adds or removes compared with a baseline version."""

    baseline_rule_ir_hash: str
    added: tuple[CandidateEvent, ...]
    removed: tuple[CandidateEvent, ...]


class BacktestReport(Frozen):
    """What a rule would have done over a stored window of one camera."""

    rule_id: str
    rule_ir_hash: str
    camera_id: str
    t0: datetime
    t1: datetime
    backtestable: Backtestable
    missing_concepts: tuple[str, ...]
    events: tuple[CandidateEvent, ...]
    count: int
    per_hour: float
    diff: BacktestDiff | None = None

    def headline(self) -> str:
        """One UI line: "would have fired 2 times earlier today", written with a times sign."""
        if self.t0.date() == self.t1.date():
            return f"would have fired {self.count}{_TIMES} earlier today"
        return (
            f"would have fired {self.count}{_TIMES} between "
            f"{self.t0:%b %d %H:%M} and {self.t1:%b %d %H:%M}"
        )


def required_concepts(rule: RuleIR, zones: dict[str, Zone]) -> dict[str, frozenset[str]]:
    """Concepts the rule's predicate needs, each with the track names that satisfy it."""
    needed: dict[str, frozenset[str]] = {}
    for _, node in walk(rule.predicate):
        if isinstance(node, StateCheck):
            needed[node.object if node.state == "present" else f"{node.state} {node.object}"] = (
                frozenset(state_concepts(node))
            )
            continue
        concepts = set(referenced_concepts(node))
        for name in referenced_zones(node):
            buffer_of = resolve_zone(name, zones, rule).buffer_of_concept
            if buffer_of is not None:
                concepts.add(buffer_of)
        for concept in concepts:
            spec = rule.concepts.get(parse_ref(concept)[0])
            needed[concept] = frozenset({concept} if spec is None else {concept, spec.prompt})
    return needed


def backtestability(
    rule: RuleIR, zones: dict[str, Zone], tracks: Sequence[Track]
) -> tuple[Backtestable, tuple[str, ...]]:
    """`yes` if every needed concept exists in the tracks, `partial` if some do, else `no`.

    Returns:
        The verdict and the sorted names of the missing concepts.
    """
    stored = {t.concept for t in tracks}
    needed = required_concepts(rule, zones)
    missing = tuple(sorted(c for c, names in needed.items() if not names & stored))
    if not missing:
        return "yes", missing
    return ("partial" if len(missing) < len(needed) else "no"), missing


def backtest(
    rule: RuleIR,
    store: TrackStore,
    camera_id: str,
    t0: datetime,
    t1: datetime,
    *,
    calibration: Calibration | None = None,
    zones: dict[str, Zone] | None = None,
    hz: float = 10.0,
    baseline: RuleIR | None = None,
    pose: PoseFeatures | None = None,
) -> BacktestReport:
    """Evaluate a rule (and optionally a baseline version) over stored tracks of one camera.

    Args:
        rule: The rule to backtest.
        store: Track store to read from; nothing is segmented.
        camera_id: Camera to backtest.
        t0: Window start.
        t1: Window end; must be after `t0`.
        calibration: Calibration of the camera.
        zones: Resolved zones by name.
        hz: Timeline rate.
        baseline: An earlier rule version to diff against ("v1 vs v2").
        pose: Pose features for pose predicates.

    Returns:
        The report: events, count, per-hour rate, backtestability and the optional diff.

    Raises:
        RuleEvaluationError: If the window is empty or invalid.
    """
    if t1 <= t0:
        msg = "backtest window must have a positive duration"
        raise RuleEvaluationError(msg)
    zone_map = zones or {}
    tracks = [t for t in store.window(camera_id, t0, t1) if t.camera_id == camera_id]
    context = DecisionContext(
        rule=rule, calibration=calibration, zones=zone_map, t0=t0, t1=t1, hz=hz
    )
    events = [e for e in context.evaluate(tracks, pose=pose) if e.camera_id == camera_id]
    diff = None
    if baseline is not None:
        before = context.model_copy(update={"rule": baseline}).evaluate(tracks, pose=pose)
        diff = diff_events(
            [e for e in before if e.camera_id == camera_id],
            events,
            baseline_rule_ir_hash=rule_ir_hash(baseline),
        )
    verdict, missing = backtestability(rule, zone_map, tracks)
    hours = (t1 - t0).total_seconds() / _SECONDS_PER_HOUR
    return BacktestReport(
        rule_id=rule.id,
        rule_ir_hash=rule_ir_hash(rule),
        camera_id=camera_id,
        t0=t0,
        t1=t1,
        backtestable=verdict,
        missing_concepts=missing,
        events=tuple(events),
        count=len(events),
        per_hour=len(events) / hours,
        diff=diff,
    )


def diff_events(
    before: Sequence[CandidateEvent],
    after: Sequence[CandidateEvent],
    *,
    baseline_rule_ir_hash: str,
    tolerance_s: float = EPISODE_GAP_S,
) -> BacktestDiff:
    """Match events of two rule versions by subject and overlapping time (within a tolerance).

    Returns:
        Events only in `after` (added) and only in `before` (removed).
    """
    added = tuple(e for e in after if not any(same_episode(e, b, tolerance_s) for b in before))
    removed = tuple(e for e in before if not any(same_episode(e, a, tolerance_s) for a in after))
    return BacktestDiff(baseline_rule_ir_hash=baseline_rule_ir_hash, added=added, removed=removed)


def same_episode(a: CandidateEvent, b: CandidateEvent, tolerance_s: float = 0.0) -> bool:
    """Whether two candidates are the same subject on one camera within `tolerance_s` in time."""
    if a.camera_id != b.camera_id or a.predicate_trace.get("subject") != b.predicate_trace.get(
        "subject"
    ):
        return False
    gap = max((a.ts_start - b.ts_end).total_seconds(), (b.ts_start - a.ts_end).total_seconds())
    return gap <= tolerance_s


def candidate_payload(candidate: CandidateEvent, context: DecisionContext) -> dict[str, object]:
    """The chain-record payload for a candidate: the event plus its decision context."""
    return {
        "candidate": candidate.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
    }


def read_candidate_payload(record: EventRecord) -> tuple[CandidateEvent, DecisionContext]:
    """Parse a payload written by `candidate_payload`.

    Raises:
        RuleEvaluationError: If the record is not a candidate or its payload is malformed.
    """
    if record.kind != "candidate":
        msg = f"record {record.seq} is a {record.kind!r} record, not a candidate"
        raise RuleEvaluationError(msg)
    try:
        candidate = CandidateEvent.model_validate(record.payload["candidate"])
        context = DecisionContext.model_validate(record.payload["context"])
    except (KeyError, ValidationError) as exc:
        msg = f"record {record.seq} has a malformed candidate payload"
        raise RuleEvaluationError(msg) from exc
    return candidate, context


def rederive(record: EventRecord, store: TrackStore, *, pose: PoseFeatures | None = None) -> bool:
    """Re-run the engine on the stored track window and compare with the recorded candidate (E9).

    Args:
        record: A `candidate` chain record whose payload came from `candidate_payload`.
        store: The track store.
        pose: The pose features used live, for rules with pose predicates.

    Returns:
        True only if the rule hash matches and re-evaluation reproduces the candidate exactly.
        A malformed or tampered payload, or changed tracks, give False.

    Raises:
        RuleEvaluationError: If the record is not a candidate record.
    """
    if record.kind != "candidate":
        msg = f"record {record.seq} is a {record.kind!r} record, not a candidate"
        raise RuleEvaluationError(msg)
    try:
        candidate, context = read_candidate_payload(record)
    except RuleEvaluationError:
        return False
    if rule_ir_hash(context.rule) != candidate.rule_ir_hash:
        return False
    tracks = store.window(candidate.camera_id, context.t0, context.t1)
    try:
        return candidate in context.evaluate(tracks, pose=pose)
    except RuleEvaluationError:
        return False
