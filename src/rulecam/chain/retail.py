"""A supermarket's checks and staff tasks as a store row on the Chain Mode scorecard.

The supermarket service (`rulecam.retail`) keeps every check and every staff task with when it
was seen and when it was fixed. Chain Mode scores stores on rule outcomes (rule, camera, time
span, compliant or not) and camera coverage. This module turns the first into the second, so a
store's retail work lands on the same HQ scorecard, with the same honesty rules (BLIND is not
compliant, small-crew floor, Wilson intervals):

| Rule | One outcome per | Compliant when |
|---|---|---|
| `retail-hazard-cleared` | spill, hazard or blocked-exit task | cleared within `hazard_minutes` |
| `retail-gap-refilled` | restock task | refilled within `restock_minutes` |
| `retail-till-answered` | queue alert sent to staff | staff answered it (acknowledged) |

A task still open is a violation once its time is up, and not counted before. The limits are the
HQ's knobs, not a model's judgement. Coverage is the recorded video of each camera: time with
recorded video is watched; anything else is BLIND (`no_video`). Nothing here sees a person.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

from rulecam.chain.models import ChainObservations, FittedRule, StoreFit, StoreObservations
from rulecam.contracts.core import Frozen, Provenance, Severity
from rulecam.perception.coverage import CoverageSegment, RuleOutcome

if TYPE_CHECKING:  # the retail package imports this module; only its types are needed here
    from rulecam.retail.models import Finding, StaffTask

PACK_ID: Final = "supermarket-retail"
PACK_VERSION: Final = 1
HAZARD: Final = "retail-hazard-cleared"
RESTOCK: Final = "retail-gap-refilled"
TILL: Final = "retail-till-answered"


class RetailLimits(Frozen):
    """The HQ's knobs: how long a store may take."""

    hazard_minutes: float = 15.0
    restock_minutes: float = 60.0


def _rules(limits: RetailLimits, cameras: Sequence[str]) -> tuple[FittedRule, ...]:
    cams = tuple(sorted(cameras))
    return (
        FittedRule(
            rule_id=HAZARD,
            status="ready",
            says=(
                "Spills, hazards and blocked exits are made safe within "
                f"{limits.hazard_minutes:g} min."
            ),
            knobs={"minutes": limits.hazard_minutes},
            severity=Severity.HIGH,
            cameras=cams,
        ),
        FittedRule(
            rule_id=RESTOCK,
            status="ready",
            says=f"Empty facings are refilled within {limits.restock_minutes:g} min.",
            knobs={"minutes": limits.restock_minutes},
            severity=Severity.LOW,
            cameras=cams,
        ),
        FittedRule(
            rule_id=TILL,
            status="ready",
            says="Staff answer every 'open another till' or 'customer needs help' alert.",
            severity=Severity.MEDIUM,
            cameras=cams,
        ),
    )


def store_fit(
    store_id: str, store_name: str, cameras: Sequence[str], limits: RetailLimits
) -> StoreFit:
    """The retail rules as a fitted pack for one store (all ready: they need no zones)."""
    rules = _rules(limits, cameras)
    digest = hashlib.sha256(
        json.dumps([r.model_dump(mode="json") for r in rules], sort_keys=True).encode()
    ).hexdigest()
    return StoreFit(
        store_id=store_id,
        store_name=store_name,
        pack_id=PACK_ID,
        pack_version=PACK_VERSION,
        pack_digest=digest,
        rules=rules,
    )


def task_outcome(task: StaffTask, limits: RetailLimits, now: datetime) -> RuleOutcome | None:
    """A task's outcome: fixed in time, late, or still open past its limit (`None`: not due)."""
    rule, minutes = (
        (RESTOCK, limits.restock_minutes)
        if task.kind == "shelf"
        else (HAZARD, limits.hazard_minutes)
    )
    seen = task.seen_at or task.created_at
    due = seen + timedelta(minutes=minutes)
    if task.status == "false_alarm":
        return None
    if task.done_at is None:
        if now < due:
            return None
        return RuleOutcome(
            rule_id=rule, camera_id=task.camera_id, start=seen, end=now, compliant=False
        )
    return RuleOutcome(
        rule_id=rule,
        camera_id=task.camera_id,
        start=seen,
        end=task.done_at,
        compliant=task.done_at <= due,
    )


def till_outcome(finding: Finding, status: str | None) -> RuleOutcome | None:
    """A queue alert's outcome: answered by staff or not (`None`: no alert was sent)."""
    if finding.kind != "queue" or finding.alert_id is None:
        return None
    at = finding.at or finding.checked_at
    return RuleOutcome(
        rule_id=TILL,
        camera_id=finding.camera_id,
        start=at,
        end=at,
        compliant=status in {"acknowledged", "false_alarm"},
    )


def store_observations(
    tasks: Sequence[StaffTask],
    findings: Sequence[Finding],
    *,
    answered: Mapping[str, str | None],
    recorded: Mapping[str, tuple[datetime, datetime]],
    limits: RetailLimits,
    now: datetime,
) -> StoreObservations:
    """One store's retail outcomes and its cameras' recorded-video coverage.

    Args:
        tasks: Staff tasks.
        findings: Checks (queue alerts are scored by whether staff answered).
        answered: Finding id -> its alert's status.
        recorded: Camera id -> (first, last) recorded instant.
        limits: The HQ's knobs.
        now: The end of the window.

    Returns:
        The observations.
    """
    outcomes = [o for t in tasks if (o := task_outcome(t, limits, now)) is not None]
    outcomes += [o for f in findings if (o := till_outcome(f, answered.get(f.id))) is not None]
    coverage = {
        cam: (CoverageSegment(start=first, end=last, watched=True),)
        for cam, (first, last) in recorded.items()
    }
    return StoreObservations(outcomes=tuple(outcomes), coverage=coverage)


def observations(
    store_id: str, observed: StoreObservations, start: datetime, end: datetime
) -> ChainObservations:
    """The store's observations as a chain window (recorded, never mixed with simulated)."""
    return ChainObservations(
        provenance=Provenance.RECORDED, start=start, end=end, stores={store_id: observed}
    )
