"""Safety-asymmetric verification policy (task P3-05, MP §1 and §3.4).

**HIGH severity: act first, verify after.** Actions (stop, power cut) run on the candidate
before any model looks at it. Verification follows. A verifier veto, an `unsure` or a
disagreement **demotes the alert to `human_queue`; it is never dropped.**

**LOW and MEDIUM: verify first.**

- Tier 1 confirms (`violation` with score at or above the threshold): alert.
- Otherwise Tier 2 is asked when the rule allows it (`spark-open-on-disagree`) and it is available.
- An alert is **suppressed only when every tier that ran rejects it with confidence** (strict AND).
  Tier 2 confirming alerts; anything else unresolved goes to `human_queue`.

The threshold is the rule's calibrated one when rule health provides it, else
`rule.verify.min_confidence`. Disputes always go to Tier 2. The scene-text firewall runs between
the tiers and the decision (see `firewall.py`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from rulecam.contracts import CandidateEvent, Dispute, RuleIR, Severity, Verifier, VerifierVerdict
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.verify.decision import Decision
from rulecam.verify.firewall import SceneTextFirewall

_log = get_logger(__name__)

_TAMPER_REASON = "text in view tried to instruct the verifier; a person reviews it"


class Tier2(Protocol):
    """What the policy needs from Tier 2 (`SparkOpenVerifier`)."""

    def verify(
        self, candidate: CandidateEvent, rule: RuleIR, tier1: VerifierVerdict | None = None
    ) -> VerifierVerdict:
        """Review a candidate."""
        ...

    def review_dispute(
        self,
        candidate: CandidateEvent,
        rule: RuleIR,
        dispute: Dispute,
        tier1: VerifierVerdict | None = None,
    ) -> Dispute:
        """Re-review a disputed alert."""
        ...


def confirms(verdict: VerifierVerdict, threshold: float) -> bool:
    """Whether a verdict confirms the violation at `threshold`."""
    return verdict.verdict == "violation" and verdict.score >= threshold


def rejects(verdict: VerifierVerdict, threshold: float) -> bool:
    """Whether a verdict confidently rejects the violation (score at most `1 - threshold`)."""
    return verdict.verdict == "no_violation" and verdict.score <= 1.0 - threshold


def _high(tier1: VerifierVerdict, tier2: VerifierVerdict | None, threshold: float) -> Decision:
    verdicts = [v for v in (tier1, tier2) if v is not None]
    if all(confirms(v, threshold) for v in verdicts):
        return Decision(alert=True, route="auto", act_first=True, reason="acted first; verified")
    return Decision(
        alert=True,
        route="human_queue",
        act_first=True,
        reason="acted first; verifier did not confirm, so a person reviews it",
    )


def _low(tier1: VerifierVerdict, tier2: VerifierVerdict | None, threshold: float) -> Decision:
    if confirms(tier1, threshold) and (tier2 is None or not rejects(tier2, threshold)):
        return Decision(alert=True, route="auto", act_first=False, reason="Tier 1 confirmed")
    if tier2 is not None and confirms(tier2, threshold) and not confirms(tier1, threshold):
        return Decision(alert=True, route="auto", act_first=False, reason="Tier 2 confirmed")
    ran = [v for v in (tier1, tier2) if v is not None]
    if all(rejects(v, threshold) for v in ran):
        return Decision(alert=False, route="auto", act_first=False, reason="every tier rejected it")
    return Decision(
        alert=False,
        route="human_queue",
        act_first=False,
        reason="tiers unsure or disagree; a person decides",
    )


def decide(
    candidate: CandidateEvent,
    rule: RuleIR,
    tier1: VerifierVerdict,
    tier2: VerifierVerdict | None = None,
    *,
    threshold: float | None = None,
) -> Decision:
    """Decide what happens to a verified candidate (a pure function).

    Args:
        candidate: The candidate (its severity picks the branch).
        rule: Its rule (for the default threshold).
        tier1: Glimmer's verdict.
        tier2: Spark Open's verdict, when it ran.
        threshold: Calibrated threshold; defaults to `rule.verify.min_confidence`.

    Returns:
        The decision. For HIGH severity `alert` is always true.
    """
    tau = rule.verify.min_confidence if threshold is None else threshold
    if candidate.severity == Severity.HIGH:
        return _high(tier1, tier2, tau)
    return _low(tier1, tier2, tau)


def needs_tier2(rule: RuleIR, tier1: VerifierVerdict, tau: float) -> bool:
    """Whether Tier 2 should review a candidate (Tier 1 did not confirm, and the rule allows it)."""
    if rule.verify.tier2 != "spark-open-on-disagree":
        return False
    return not confirms(tier1, tau)


class VerificationPolicy:
    """The runtime `Policy`: act-first for HIGH, Tier 2 escalation, firewall, then `decide`."""

    def __init__(
        self,
        *,
        tier1: Verifier,
        tier2: Tier2 | None = None,
        firewall: SceneTextFirewall | None = None,
        thresholds: Mapping[str, float] | None = None,
    ) -> None:
        """Create the policy.

        Args:
            tier1: Glimmer verifier (the firewall re-runs it on blurred strips).
            tier2: Spark Open verifier, if configured.
            firewall: Scene-text firewall, if configured.
            thresholds: Calibrated thresholds by rule id (from rule health).
        """
        self._tier1 = tier1
        self._tier2 = tier2
        self._firewall = firewall
        self._thresholds = dict(thresholds or {})

    def threshold(self, rule: RuleIR) -> float:
        """Return the rule's calibrated threshold, else its `min_confidence`."""
        return self._thresholds.get(rule.id, rule.verify.min_confidence)

    def act_first(self, candidate: CandidateEvent, rule: RuleIR) -> bool:  # noqa: ARG002 - Policy protocol
        """HIGH severity acts before verification."""
        return candidate.severity == Severity.HIGH

    def decide(self, candidate: CandidateEvent, rule: RuleIR, tier1: VerifierVerdict) -> Decision:
        """Escalate if needed, run the firewall, then decide.

        Args:
            candidate: The candidate with its strip.
            rule: Its rule.
            tier1: Glimmer's verdict from the runtime loop.

        Returns:
            The decision.
        """
        tau = self.threshold(rule)
        tier2 = None
        try:
            if self._tier2 is not None and needs_tier2(rule, tier1, tau):
                tier2 = self._tier2.verify(candidate, rule, tier1)
            if self._firewall is not None:
                screened = self._firewall.screen(candidate, rule, tier1, tier2)
                if screened.flagged:
                    if screened.force_human:
                        return self._to_human(candidate, rule, _TAMPER_REASON)
                    tier1, tier2 = screened.tier1, screened.tier2
        except (RulecamError, OSError, ImportError) as exc:
            _log.warning(
                "policy_verification_error", event_id=candidate.event_id, error=type(exc).__name__
            )
            return self._to_human(candidate, rule, "verification error; a person reviews it")
        decision = decide(candidate, rule, tier1, tier2, threshold=tau)
        _log.info(
            "policy_decision",
            event_id=candidate.event_id,
            alert=decision.alert,
            route=decision.route,
        )
        return decision

    def _to_human(self, candidate: CandidateEvent, rule: RuleIR, reason: str) -> Decision:
        """An alert a person must review (never dropped, whatever the severity)."""
        return Decision(
            alert=True,
            route="human_queue",
            act_first=self.act_first(candidate, rule),
            reason=reason,
        )

    def review_dispute(
        self,
        candidate: CandidateEvent,
        rule: RuleIR,
        dispute: Dispute,
        tier1: VerifierVerdict | None = None,
    ) -> Dispute:
        """Send a dispute to Tier 2 (always); without Tier 2 it stays pending for a person.

        Args:
            candidate: The disputed candidate.
            rule: Its rule.
            dispute: The worker's dispute.
            tier1: Glimmer's verdict, if any.

        Returns:
            The reviewed dispute.
        """
        if self._tier2 is None:
            return dispute.model_copy(update={"outcome": "pending"})
        return self._tier2.review_dispute(candidate, rule, dispute, tier1)
