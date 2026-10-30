"""Candidates, verdicts, actions and the hash-chained record (task book §4.4, MP §7.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from rulecam.contracts.core import Frozen, Measurement, Severity


class CandidateEvent(Frozen):
    """A rule (or surprise) firing, before verification."""

    event_id: str
    rule_id: str  # or "surprise"
    camera_id: str
    ts_start: datetime
    ts_end: datetime
    severity: Severity
    measurement: Measurement | None = None
    predicate_trace: dict[str, float | str | bool]
    strip_paths: tuple[str, ...]  # 6-frame strip (skeleton renders when anonymous)
    track_window_hash: str
    plan_version_hash: str
    rule_ir_hash: str


class VerifierVerdict(Frozen):
    """A verifier's judgement on one candidate."""

    tier: Literal["glimmer", "spark_open"]
    verdict: Literal["violation", "no_violation", "unsure"]
    score: float  # P(violation) in [0, 1]
    reason: str
    reasoning: str | None = None  # Spark Open reasoning_content, verbatim
    scene_text: str = ""  # text visible in frame, reported separately (firewall)
    tamper: bool = False
    latency_ms: int
    model_version: str


class ActionRecord(Frozen):
    """What the response agent did, and who approved it."""

    kind: Literal[
        "speak",
        "visual_signal",
        "coach_station",
        "notify",
        "escalate",
        "interlock_cut",
        "interlock_resume",
        "log",
    ]
    role: str | None = None
    gate: Literal["auto", "human"]
    approved_by_role: str | None = None
    ts: datetime
    ok: bool
    detail: str = ""


class Ack(Frozen):
    """An acknowledgement of an alert by a role (never a named person)."""

    role: str
    ts: datetime


class Dispute(Frozen):
    """A worker's anonymous dispute of an alert."""

    channel: Literal["tap", "voice"]
    reason_text: str
    outcome: Literal["pending", "upheld", "rejected"] = "pending"
    reviewer_reasoning: str | None = None


class EventRecord(Frozen):
    """One append-only, hash-chained log line (MP §7.4).

    `hash = sha256(prev_hash + canonical JSON of the record without hash)`.
    """

    seq: int
    kind: Literal[
        "candidate",
        "verdict",
        "action",
        "ack",
        "dispute",
        "label",
        "plan",
        "rule_change",
        "coverage",
        "advice",
        "measurement_plan",
        "sealed_cause",
        "surprise",
        "tamper_attempt",
    ]
    ts: datetime
    site: str
    payload: dict[str, object]
    prev_hash: str
    hash: str
