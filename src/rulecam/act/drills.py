"""Response drills (task P3-S2): labelled practice alerts through the real escalation chain.

A drill starts like a real alert (same notifier, same escalation timers, same buttons), but every
message says **DRILL** and its start is chained as a `label` record with `drill: true`. Response
time is measured from the chain alone: drill start → first `ack` record. Results are grouped
**by role, never by person** (acks carry roles only), and they feed the F689 supervision log as an
evidence-pack row.
"""

from __future__ import annotations

import itertools
import statistics
import threading
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from rulecam.act.escalation import EscalationManager
from rulecam.contracts import Clock, EventRecord, Frozen
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.evidence.packs import PackRow
from rulecam.log import get_logger

_log = get_logger(__name__)

DRILL_PREFIX = "drill-"
DEFAULT_TARGET_S = 60.0
ROLE_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"


class DrillStart(BaseModel):
    """Body of `POST /drills`."""

    roles: list[Annotated[str, Field(pattern=ROLE_PATTERN)]] = Field(min_length=1, max_length=5)
    after_s: float = Field(default=60.0, ge=5.0, le=900.0)
    scenario: str = Field(default="Person down in the east corridor", min_length=3, max_length=120)


class DrillStarted(BaseModel):
    """Reply of `POST /drills`."""

    event_id: str
    seq: int


class RoleTiming(Frozen):
    """Acknowledgement times for one role (never one person)."""

    role: str
    acked: int
    median_s: float
    p90_s: float
    max_s: float


class DrillReport(Frozen):
    """All drills in one chain file."""

    drills: int
    acked: int
    missed: int
    within_target: int
    target_s: float
    by_role: tuple[RoleTiming, ...]


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def drill_report(records: Sequence[EventRecord], target_s: float = DEFAULT_TARGET_S) -> DrillReport:
    """Summarise drills from chain records: time from each drill start to its first ack.

    Args:
        records: A chain file's records.
        target_s: The site's acknowledgement target.

    Returns:
        Totals and per-role timings (roles only).
    """
    starts: dict[str, datetime] = {
        str(r.payload["event_id"]): r.ts
        for r in records
        if r.kind == "label" and r.payload.get("drill") is True and "event_id" in r.payload
    }
    first_ack: dict[str, tuple[str, float]] = {}
    for r in records:
        event_id = str(r.payload.get("event_id", ""))
        if r.kind == "ack" and event_id in starts and event_id not in first_ack:
            first_ack[event_id] = (
                str(r.payload.get("role", "?")),
                (r.ts - starts[event_id]).total_seconds(),
            )
    by_role: dict[str, list[float]] = {}
    for role, seconds in first_ack.values():
        by_role.setdefault(role, []).append(seconds)
    timings = tuple(
        RoleTiming(
            role=role,
            acked=len(times),
            median_s=round(statistics.median(times), 1),
            p90_s=round(_percentile(times, 0.9), 1),
            max_s=round(max(times), 1),
        )
        for role, times in sorted(by_role.items())
    )
    return DrillReport(
        drills=len(starts),
        acked=len(first_ack),
        missed=len(starts) - len(first_ack),
        within_target=sum(1 for _, s in first_ack.values() if s <= target_s),
        target_s=target_s,
        by_role=timings,
    )


def f689_row(report: DrillReport) -> PackRow:
    """The drills as one F689 supervision-log row (drills are not camera-watched, so 100%)."""
    actions = tuple(
        f"{t.role}: median {t.median_s:g} s, p90 {t.p90_s:g} s ({t.acked} acked)"
        for t in report.by_role
    )
    missed = (f"{report.missed} drill(s) never acknowledged",) if report.missed else ()
    return PackRow(
        clause="F689 supervision: response drills",
        rule_id="response-drills",
        rule_text=f"Acknowledge a drill alert within {report.target_s:g} s",
        checks=report.drills,
        passes=report.within_target,
        watched_fraction=1.0,
        corrective_actions=actions + missed,
    )


class DrillRunner:
    """Starts labelled drills through the real escalation chain."""

    def __init__(self, escalation: EscalationManager, chain: EventChain, clock: Clock) -> None:
        """Create the runner.

        Args:
            escalation: The site's escalation manager (the same one real alerts use).
            chain: The evidence chain (drill labels and acks).
            clock: Time source.
        """
        self._escalation = escalation
        self._chain = chain
        self._clock = clock
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def start(self, body: DrillStart) -> DrillStarted:
        """Label the drill on the chain, then notify the first role through the real chain."""
        with self._lock:
            event_id = f"{DRILL_PREFIX}{self._clock.now():%Y%m%dT%H%M%S}-{next(self._ids)}"
        record = self._chain.append(
            "label",
            {"drill": True, "event_id": event_id, "scenario": body.scenario, "roles": body.roles},
        )
        self._escalation.start(
            event_id,
            body.roles,
            after_s=body.after_s,
            title=f"DRILL: {body.scenario}",
            body="This is a practice alert. Respond as you would for real; it is timed by role.",
        )
        _log.info("drill_started", event_id=event_id, roles=body.roles)
        return DrillStarted(event_id=event_id, seq=record.seq)

    def report(
        self, day: datetime | None = None, target_s: float = DEFAULT_TARGET_S
    ) -> DrillReport:
        """The drill report for `day` (default today, UTC)."""
        path = self._chain.path_for((day or self._clock.now()).date())
        return drill_report(read_chain(path) if path.is_file() else [], target_s)


def drills_router(runner: DrillRunner) -> APIRouter:
    """`POST /drills` (start one) and `GET /drills/report` (today's timings by role).

    Mount it under the owner-only alerts router: starting a drill notifies real phones.
    """
    router = APIRouter(prefix="/drills", tags=["drills"])

    @router.post("", status_code=status.HTTP_201_CREATED)
    def start(body: DrillStart) -> DrillStarted:
        return runner.start(body)

    @router.get("/report")
    def report(
        target_s: Annotated[float, Query(ge=5.0, le=3600.0)] = DEFAULT_TARGET_S,
    ) -> DrillReport:
        return runner.report(target_s=target_s)

    return router
