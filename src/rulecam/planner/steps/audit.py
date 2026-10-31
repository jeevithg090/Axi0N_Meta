"""Step 9: Spark Open plan audit with stored reasoning, then Glimmer back-translation (P2-07).

Spark Open (`SparkOpenVerifier.audit_plan`, P3-04) reviews the compiled plan at medium effort
for gaps, contradictions and over-reach. Each `AuditFinding` carries the id of Spark Open's
reasoning, stored verbatim and content-addressed under `data/reasoning/<id>.txt`. When Spark
Open is not configured or the call fails, the plan gets a single `audit_skipped: ...` finding,
so a plan that was not audited never looks like a clean audit. Glimmer then back-translates
the rules (`back_translate.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol

from rulecam.clients.errors import ModelClientError
from rulecam.contracts import StructuredLLM
from rulecam.contracts.plan import AuditFinding, SitePlan
from rulecam.log import get_logger
from rulecam.planner.steps.back_translate import (
    NOT_BACK_TRANSLATED,
    back_translate_plan,
    plan_languages,
)

_log = get_logger(__name__)

AUDIT_SKIPPED_PREFIX: Final = "audit_skipped"


class PlanAuditor(Protocol):
    """What the audit step needs from Spark Open (`SparkOpenVerifier` satisfies it)."""

    def audit_plan(self, plan: SitePlan) -> list[AuditFinding]:
        """Return one finding per gap; raise `ModelClientError` if the audit could not run."""
        ...


def _skipped(reason: str) -> tuple[AuditFinding, ...]:
    return (AuditFinding(by="spark-open", text=f"{AUDIT_SKIPPED_PREFIX}: {reason}"),)


def audit_plan(plan: SitePlan, auditor: PlanAuditor | None) -> tuple[AuditFinding, ...]:
    """Audit a plan with Spark Open.

    Args:
        plan: The accepted plan (step 8 output).
        auditor: Spark Open's plan auditor, or `None` when it is not configured.

    Returns:
        Spark Open's findings (empty when it found no gaps), or a single `audit_skipped`
        finding when the audit did not run.
    """
    if auditor is None:
        _log.warning("plan_audit_skipped", plan_id=plan.plan_id, reason="not configured")
        return _skipped("not audited, Spark Open is not configured")
    try:
        return tuple(auditor.audit_plan(plan))
    except (ModelClientError, OSError) as exc:
        _log.warning("plan_audit_skipped", plan_id=plan.plan_id, error=repr(exc))
        return _skipped(f"not audited, Spark Open failed ({type(exc).__name__})")


def is_audit_skipped(plan: SitePlan) -> bool:
    """Return whether the plan carries an `audit_skipped` finding."""
    return any(finding.text.startswith(AUDIT_SKIPPED_PREFIX) for finding in plan.audit)


def audit_summary(plan: SitePlan) -> str:
    """Describe step 9's outcome for its `planner.step` detail.

    Args:
        plan: The plan after the audit step.

    Returns:
        E.g. "2 audit findings; back-translated (en, es)" or "audit_skipped: ...".
    """
    skipped = is_audit_skipped(plan)
    audit = plan.audit[0].text if skipped else f"{len(plan.audit)} audit findings"
    languages = ", ".join(plan_languages(plan))
    if not plan.back_translation or NOT_BACK_TRANSLATED in plan.back_translation:
        return f"{audit}; back-translation incomplete ({languages})"
    return f"{audit}; back-translated ({languages})"


def audit_hook(
    auditor: PlanAuditor | None, glimmer: StructuredLLM | None, *, glimmer_model: str
) -> Callable[[SitePlan], SitePlan]:
    """Build the pipeline's step 9 hook: Spark Open audit, then Glimmer back-translation.

    Args:
        auditor: Spark Open's plan auditor (`SparkOpenVerifier`), or `None`.
        glimmer: Glimmer (`GlimmerClient`), or `None`.
        glimmer_model: Glimmer's model name (`GlimmerClient.model`).

    Returns:
        A `SitePlan -> SitePlan` hook that fills `audit` and `back_translation`.
    """

    def hook(plan: SitePlan) -> SitePlan:
        findings = audit_plan(plan, auditor)
        text = back_translate_plan(plan, glimmer, model=glimmer_model)
        return plan.model_copy(update={"audit": findings, "back_translation": text})

    return hook
