"""Pre-registration of "did it work?" checks on the evidence chain (P3-10, SA §4 steps 15-16).

When the owner accepts a card, P4's `MeasurementPlan` (design, blocks, minimum detectable effect,
decision rule) is canonicalised and hashed, and a `measurement_plan` record is appended **before**
the card becomes `measuring`. Switchback blocks and the final verdict are appended after it, so
anyone can check from the chain that the method was fixed before the first block ran.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from rulecam.advisor.errors import AdvisorError
from rulecam.contracts import EventRecord, MeasurementPlan, Recommendation, Verdict
from rulecam.evidence.chain import EventChain, canonical_json
from rulecam.log import get_logger

_log = get_logger(__name__)


class PreregistrationError(AdvisorError):
    """A card cannot be pre-registered (wrong status or a plan for another card)."""


def plan_hash(plan: MeasurementPlan) -> str:
    """SHA-256 of the plan's canonical JSON, without its own `preregistration_hash` field."""
    body = plan.model_dump(mode="json", exclude={"preregistration_hash"})
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


class AdviceLedger:
    """Writes the advice lifecycle to the hash-chained log."""

    def __init__(self, chain: EventChain) -> None:
        """Create the ledger.

        Args:
            chain: The site's evidence chain.
        """
        self._chain = chain

    def accept(
        self, recommendation: Recommendation, plan: MeasurementPlan
    ) -> tuple[Recommendation, MeasurementPlan, EventRecord]:
        """Pre-register the plan, then mark the card `measuring`.

        Args:
            recommendation: A `proposed` (or `accepted`) card.
            plan: Its measurement plan from P4.

        Returns:
            The card (now `measuring`, plan id set), the hashed plan and the chain record.

        Raises:
            PreregistrationError: If the plan is for another card or the card is past acceptance.
        """
        if plan.recommendation_id != recommendation.id:
            msg = f"plan {plan.id} is for {plan.recommendation_id}, not {recommendation.id}"
            raise PreregistrationError(msg)
        if recommendation.status not in {"proposed", "accepted"}:
            msg = f"card {recommendation.id} is already {recommendation.status}"
            raise PreregistrationError(msg)
        hashed = plan.model_copy(update={"preregistration_hash": plan_hash(plan)})
        record = self._chain.append(
            "measurement_plan",
            {"recommendation_id": recommendation.id, "plan": hashed.model_dump(mode="json")},
        )
        measuring = recommendation.model_copy(
            update={"status": "measuring", "measurement_plan_id": hashed.id}
        )
        _log.info("advice_preregistered", recommendation_id=recommendation.id, seq=record.seq)
        return measuring, hashed, record

    def record_block(self, plan: MeasurementPlan, block: dict[str, object]) -> EventRecord:
        """Append one switchback block's measurement (after pre-registration)."""
        return self._chain.append("advice", {"type": "block", "plan_id": plan.id, "block": block})

    def record_verdict(self, verdict: Verdict) -> EventRecord:
        """Append the final verdict (worked / no effect / worse / not enough data)."""
        return self._chain.append("advice", {"type": "verdict", **verdict.model_dump(mode="json")})


def _plan_id(record: EventRecord) -> str | None:
    plan = record.payload.get("plan") if record.kind == "measurement_plan" else None
    return str(plan.get("id")) if isinstance(plan, dict) else None


def preregistered_first(records: Sequence[EventRecord], plan_id: str) -> bool:
    """Whether the plan's pre-registration precedes its first block, and its hash still holds.

    Args:
        records: A chain file's records.
        plan_id: The measurement plan id.

    Returns:
        True only if a matching pre-registration exists, its stored hash matches the plan's
        contents, and every block of that plan has a higher seq.
    """
    prereg = next((r for r in records if _plan_id(r) == plan_id), None)
    if prereg is None:
        return False
    plan = MeasurementPlan.model_validate(prereg.payload["plan"])
    if plan.preregistration_hash != plan_hash(plan):
        return False
    blocks = [
        r.seq
        for r in records
        if r.kind == "advice"
        and r.payload.get("type") == "block"
        and r.payload.get("plan_id") == plan_id
    ]
    return all(seq > prereg.seq for seq in blocks)
