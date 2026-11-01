"""Turn a run's evidence-chain records and bus frames into scored observations.

A candidate is what the rules engine fired (`candidate` record). Its verdicts follow as
`verdict` records with the same `event_id`: a Tier 1 (Glimmer) verdict, perhaps a Tier 2
(Spark Open) one, and, for act-first rules, a `decision`. A candidate is an **alert** (a
verified alert, the thing the owner sees) when:

1. its chained `decision` says `alert: true`; or, without a decision,
2. its last verifier verdict (Tier 2 over Tier 1) is `violation`; or
3. an `alert.new` frame for its event reached the bus.

The alert time is the earliest of: the bus frame's arrival, or the chain record that made it an
alert. Candidate times are camera time; the run's timeline maps them to clip seconds.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from pydantic import ValidationError

from rulecam.contracts import CandidateEvent, Frozen
from rulecam.eval.record import BusFrame, RunRecord, Timeline
from rulecam.log import get_logger

_log = get_logger(__name__)

ALERT_TOPIC = "alert.new"
VerdictLabel = str  # "violation" | "no_violation" | "unsure"


class Observed(Frozen):
    """One candidate of the scored pass, in clip seconds, with what the verifiers said."""

    event_id: str
    rule_id: str
    start_s: float
    end_s: float
    cam_start: datetime
    tier1: VerdictLabel | None = None
    tier2: VerdictLabel | None = None
    alert: bool = False
    alert_at: datetime | None = None


def _ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class _Verdicts:
    """Verifier results of one event, folded in chain order."""

    def __init__(self) -> None:
        self.tier1: str | None = None
        self.tier2: str | None = None
        self.decision: bool | None = None
        self.decided_at: datetime | None = None
        self.verdict_at: datetime | None = None

    def add(self, payload: Mapping[str, object], at: datetime | None) -> None:
        decision = payload.get("decision")
        if isinstance(decision, Mapping):
            self.decision = bool(decision.get("alert"))
            self.decided_at = at
            return
        tier, verdict = payload.get("tier"), payload.get("verdict")
        if not isinstance(verdict, str):
            return
        if tier == "spark_open":
            self.tier2 = verdict
        else:
            self.tier1 = verdict
        self.verdict_at = at

    def alert(self) -> tuple[bool, datetime | None]:
        if self.decision is not None:
            return self.decision, self.decided_at
        final = self.tier2 or self.tier1
        return final == "violation", self.verdict_at


def _candidates(
    chain: Iterable[Mapping[str, object]], camera_id: str, plan_hash: str | None
) -> dict[str, CandidateEvent]:
    found: dict[str, CandidateEvent] = {}
    for record in chain:
        if record.get("kind") != "candidate":
            continue
        payload = record.get("payload")
        raw = payload.get("candidate") if isinstance(payload, Mapping) else None
        try:
            candidate = CandidateEvent.model_validate(raw)
        except ValidationError:
            _log.warning("eval_candidate_unreadable", seq=record.get("seq"))
            continue
        if candidate.camera_id != camera_id:
            continue
        if plan_hash is not None and candidate.plan_version_hash != plan_hash:
            continue
        found[candidate.event_id] = candidate
    return found


def _verdicts(chain: Iterable[Mapping[str, object]], ids: set[str]) -> dict[str, _Verdicts]:
    folded: dict[str, _Verdicts] = {}
    for record in chain:
        payload = record.get("payload")
        if record.get("kind") != "verdict" or not isinstance(payload, Mapping):
            continue
        event_id = payload.get("event_id")
        if isinstance(event_id, str) and event_id in ids:
            folded.setdefault(event_id, _Verdicts()).add(payload, _ts(record.get("ts")))
    return folded


def _bus_alerts(frames: Iterable[BusFrame]) -> dict[str, datetime]:
    first: dict[str, datetime] = {}
    for frame in frames:
        event_id = frame.payload.get("event_id")
        if frame.topic == ALERT_TOPIC and isinstance(event_id, str):
            first[event_id] = min(frame.at, first.get(event_id, frame.at))
    return first


def _earliest(*times: datetime | None) -> datetime | None:
    present = [t for t in times if t is not None]
    return min(present) if present else None


def observe(run: RunRecord) -> list[Observed]:
    """Return the run's candidates that fall inside its scored pass, in clip-time order."""
    timeline: Timeline = run.timeline
    candidates = _candidates(run.chain, run.camera_id, run.plan_version_hash)
    verdicts = _verdicts(run.chain, set(candidates))
    bus = _bus_alerts(run.bus)
    out = []
    for event_id, candidate in candidates.items():
        span = timeline.clip_span(candidate.ts_start, candidate.ts_end)
        if span is None:
            continue  # fired on chunks outside the scored pass (survey or drain)
        start, end = span
        folded = verdicts.get(event_id, _Verdicts())
        alert, at = folded.alert()
        bus_at = bus.get(event_id)
        out.append(
            Observed(
                event_id=event_id,
                rule_id=candidate.rule_id,
                start_s=start,
                end_s=max(start, end),
                cam_start=candidate.ts_start,
                tier1=folded.tier1,
                tier2=folded.tier2,
                alert=alert or bus_at is not None,
                alert_at=_earliest(at if alert else None, bus_at),
            )
        )
    return sorted(out, key=lambda o: (o.start_s, o.event_id))
