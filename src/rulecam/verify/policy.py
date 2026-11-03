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

**Without Tier 2** (no Baseten key) the same rules apply with Tier 1 alone: Tier 1 confirming
alerts, Tier 1 confidently rejecting suppresses, and anything else (unsure, low score) goes to
`human_queue`. Nothing passes silently. A dispute then stays `pending` for a person.

**When Tier 1 fails** (no verdict: Glimmer unreachable, a timeout, a bad reply, or its circuit is
open; `verify.guard`), the loop records an `unsure` error verdict (`verifier_error: …`) and
`decide_unverified` applies, for every severity: **the alert goes to `human_queue`** ("verifier
unavailable — <reason>"), never suppressed and never left undecided. For HIGH the actions already
ran; a person reviews them. When the rule allows Tier 2 and it is configured, Tier 2 reviews the
candidate first (without a Tier 1 description of the frames): if it confirms, the alert goes out
(`auto`, after the firewall); anything else, including a confident Tier 2 rejection, still goes
to a person, because one tier alone never suppresses an alert whose Tier 1 check could not run.

**Model decides** (prototype mode, `model_decides`): the verdict of the strongest tier that ran
(Tier 2 when it ran, else Tier 1) is final, whatever the severity: `violation` alerts,
`no_violation` is suppressed, `unsure` alerts when its score is at least `UNSURE_ALERT_SCORE`.
HIGH rules still act first. Only a candidate no model could judge (Tier 1 failed) or that the
firewall flags as tampering goes to a person.

**Without the firewall** (`screen_required`: the runtime wanted it but Prompt Guard 2 is not
available) scene text cannot be screened, so a candidate whose verdicts report scene text is
never suppressed on those verdicts: it goes to `human_queue` instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from rulecam.contracts import CandidateEvent, Dispute, RuleIR, Severity, Verifier, VerifierVerdict
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.verify.decision import Decision
from rulecam.verify.firewall import SceneTextFirewall
from rulecam.verify.guard import verifier_error

_log = get_logger(__name__)

_TAMPER_REASON = "text in view tried to instruct the verifier; a person reviews it"
_UNSCREENED_REASON = "text in view could not be screened (firewall degraded); a person reviews it"
NO_TIER2_REASONING = "no Tier 2 reviewer is configured; a person decides"
UNAVAILABLE = "verifier unavailable — "
UNSURE_ALERT_SCORE = 0.5


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


def _model(tier1: VerifierVerdict, tier2: VerifierVerdict | None, *, act_first: bool) -> Decision:
    """Model decides: the strongest tier's verdict is final."""
    final = tier2 if tier2 is not None else tier1
    alert = final.verdict == "violation" or (
        final.verdict == "unsure" and final.score >= UNSURE_ALERT_SCORE
    )
    reason = f"{final.tier} decided: {final.verdict} ({final.score:.2f})"
    return Decision(alert=alert, route="auto", act_first=act_first, reason=reason)


def decide_unverified(
    candidate: CandidateEvent,
    rule: RuleIR,
    error: str,
    tier2: VerifierVerdict | None = None,
    *,
    threshold: float | None = None,
) -> Decision:
    """Decide a candidate whose Tier 1 verification failed (a pure function).

    Args:
        candidate: The candidate (HIGH already acted first).
        rule: Its rule (for the default threshold).
        error: Why Tier 1 failed.
        tier2: Spark Open's verdict, when it reviewed the candidate instead.
        threshold: Calibrated threshold; defaults to `rule.verify.min_confidence`.

    Returns:
        An alert: `auto` only when Tier 2 confirmed it, else `human_queue`. Never suppressed.
    """
    tau = rule.verify.min_confidence if threshold is None else threshold
    act_first = candidate.severity == Severity.HIGH
    if tier2 is not None and confirms(tier2, tau):
        reason = f"Tier 2 confirmed; Tier 1 unavailable ({error})"
        return Decision(alert=True, route="auto", act_first=act_first, reason=reason)
    seen = f"; Tier 2 said {tier2.verdict} ({tier2.score:.2f})" if tier2 is not None else ""
    return Decision(
        alert=True, route="human_queue", act_first=act_first, reason=f"{UNAVAILABLE}{error}{seen}"
    )


def decide(
    candidate: CandidateEvent,
    rule: RuleIR,
    tier1: VerifierVerdict,
    tier2: VerifierVerdict | None = None,
    *,
    threshold: float | None = None,
    model_decides: bool = False,
) -> Decision:
    """Decide what happens to a verified candidate (a pure function).

    Args:
        candidate: The candidate (its severity picks the branch).
        rule: Its rule (for the default threshold).
        tier1: Glimmer's verdict (an error verdict means Tier 1 failed: `decide_unverified`).
        tier2: Spark Open's verdict, when it ran.
        threshold: Calibrated threshold; defaults to `rule.verify.min_confidence`.
        model_decides: Prototype mode: the strongest tier's verdict is final (`_model`).

    Returns:
        The decision. For HIGH severity, and whenever Tier 1 failed, `alert` is always true,
        except under `model_decides`.
    """
    tau = rule.verify.min_confidence if threshold is None else threshold
    error = verifier_error(tier1)
    if error is not None:
        return decide_unverified(candidate, rule, error, tier2, threshold=tau)
    if model_decides:
        return _model(tier1, tier2, act_first=candidate.severity == Severity.HIGH)
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
        screen_required: bool = False,
        model_decides: bool = False,
    ) -> None:
        """Create the policy.

        Args:
            tier1: Glimmer verifier (the firewall re-runs it on blurred strips).
            tier2: Spark Open verifier, if configured.
            firewall: Scene-text firewall, if configured.
            thresholds: Calibrated thresholds by rule id (from rule health).
            screen_required: Scene text must be screened; without a firewall, a candidate with
                scene text is sent to a person instead of being suppressed.
            model_decides: Prototype mode: the verifiers' verdict is final (see the module).
        """
        self._tier1 = tier1
        self._tier2 = tier2
        self._firewall = firewall
        self._thresholds = dict(thresholds or {})
        self._screen_required = screen_required and not model_decides
        self._model_decides = model_decides

    @property
    def has_tier2(self) -> bool:
        """Whether a Tier 2 reviewer is configured."""
        return self._tier2 is not None

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
        error = verifier_error(tier1)
        if error is not None:
            return self._unverified(candidate, rule, tier1, error)
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
        decision = decide(
            candidate, rule, tier1, tier2, threshold=tau, model_decides=self._model_decides
        )
        if self._unscreened(decision, tier1, tier2):
            return self._to_human(candidate, rule, _UNSCREENED_REASON)
        _log.info(
            "policy_decision",
            event_id=candidate.event_id,
            alert=decision.alert,
            route=decision.route,
        )
        return decision

    def _unverified(
        self, candidate: CandidateEvent, rule: RuleIR, failed: VerifierVerdict, error: str
    ) -> Decision:
        """Tier 1 failed: Tier 2 may confirm (then the firewall screens it); else a person."""
        tau = self.threshold(rule)
        tier2 = None
        try:
            if self._tier2 is not None and needs_tier2(rule, failed, tau):
                tier2 = self._tier2.verify(candidate, rule, None)  # no Tier 1 description
            decision = decide_unverified(candidate, rule, error, tier2, threshold=tau)
            wall = self._firewall if decision.route == "auto" else None
            if wall is not None and wall.screen(candidate, rule, failed, tier2).flagged:
                return self._to_human(candidate, rule, _TAMPER_REASON)
        except (RulecamError, OSError, ImportError) as exc:
            _log.warning(
                "policy_tier2_error", event_id=candidate.event_id, error=type(exc).__name__
            )
            decision = decide_unverified(candidate, rule, error, threshold=tau)
        _log.warning(
            "policy_unverified",
            event_id=candidate.event_id,
            route=decision.route,
            tier2=tier2.verdict if tier2 is not None else None,
            error=error,
        )
        return decision

    def _unscreened(
        self, decision: Decision, tier1: VerifierVerdict, tier2: VerifierVerdict | None
    ) -> bool:
        """A suppression that rests on verdicts whose scene text nobody could screen."""
        suppressed = not decision.alert and decision.route == "auto"
        if not suppressed or self._firewall is not None or not self._screen_required:
            return False
        return any(v.scene_text.strip() for v in (tier1, tier2) if v is not None)

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
            return dispute.model_copy(
                update={"outcome": "pending", "reviewer_reasoning": NO_TIER2_REASONING}
            )
        return self._tier2.review_dispute(candidate, rule, dispute, tier1)
