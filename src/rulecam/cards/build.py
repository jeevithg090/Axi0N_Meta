"""Read rule cards and declined cards out of a saved plan (task P2-10).

Every field comes from the `SitePlan` the planner saved (`plans/<id>.plan.yaml`):

- plain language: Glimmer's back-translation (P2-07) of the rule's IR, in the crew language;
- cited standards: the rule's `source.standard`, verified by its `citation_text_match` check;
- feasibility: the verdict of the concern whose `concern_covered` check names the rule;
- Rule CI: the rule's `ci_green` check (P2-08/09), whose detail starts with
  `RuleCIReport.summary()`: `boundary 16/16, mutations 5/5; ...; 18 lookalikes, fooled 1 ...`;
- calibrated false-alarm rate: the rule's `verify.calibrated_alpha`;
- Spark Open: the plan's audit findings. The audit hook always writes a back-translation, so an
  empty audit with a back-translation means Spark Open found nothing, and no back-translation
  means the audit step did not run.

A declined card never shows the request (`FeasibilityVerdict.concern`). A decline made by the
deterministic guardrail carries a fixed policy reason, which names its category; a model
decline's reason and alternative are model text, so every request word that is not policy
vocabulary is replaced by `[redacted]`, which removes names the model may have repeated.
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import quote, urlsplit

from rulecam.cards.errors import CardError
from rulecam.cards.models import (
    DeclinedCardData,
    LookalikeNumbers,
    RuleCardData,
    RuleCINumbers,
    SparkOpenAudit,
)
from rulecam.contracts.plan import AcceptanceCheck, FeasibilityVerdict, SitePlan
from rulecam.contracts.rule_ir import RuleIR
from rulecam.planner.guardrails import curated_decline, policy_words
from rulecam.planner.pipeline import PlanStore
from rulecam.planner.steps.audit import is_audit_skipped
from rulecam.planner.steps.back_translate import NOT_BACK_TRANSLATED, plan_languages

REDACTED: Final = "[redacted]"
_WORD: Final = re.compile(r"\w+")
_CI_HEAD: Final = re.compile(r"boundary (\d+)/(\d+), mutations (\d+)/(\d+)")
_LOOKALIKES: Final = re.compile(r"(?:^|; )(\d+) lookalikes, fooled (\d+)")
_SECTION: Final = re.compile(r"\[([^\]\n]+)\]")


def back_translation_for(plan: SitePlan, rule_id: str, lang: str) -> str | None:
    """Return the rule's line from the `[lang]` section of the plan's back-translation.

    Args:
        plan: The plan.
        rule_id: The rule.
        lang: The section's language code.

    Returns:
        The summary (possibly a `not back-translated (...)` marker), or `None` when absent.
    """
    section = None
    prefix = f"- {rule_id}: "
    for line in plan.back_translation.splitlines():
        header = _SECTION.fullmatch(line.strip())
        if header is not None:
            section = header.group(1)
        elif section == lang and line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


def _checks(plan: SitePlan, rule_id: str, check: str) -> list[AcceptanceCheck]:
    return [c for c in plan.acceptance.checks if c.item == rule_id and c.check == check]


def rule_ci_numbers(plan: SitePlan, rule_id: str) -> RuleCINumbers | None:
    """Return the rule's Rule CI numbers from its `ci_green` check, or `None` if none ran.

    Args:
        plan: The plan.
        rule_id: The rule.

    Returns:
        Boundary, mutation and lookalike counts with the verdict.
    """
    checks = _checks(plan, rule_id, "ci_green")
    head = _CI_HEAD.match(checks[-1].detail) if checks else None
    if head is None:
        return None
    passed, total, killed, mutants = (int(n) for n in head.groups())
    found = _LOOKALIKES.search(checks[-1].detail)
    lookalikes = (
        LookalikeNumbers(tested=int(found.group(1)), fooled=int(found.group(2)))
        if found is not None
        else None
    )
    return RuleCINumbers(
        boundary_passed=passed,
        boundary_total=total,
        mutants_killed=killed,
        mutants_total=mutants,
        lookalikes=lookalikes,
        ci_green=checks[-1].passed,
    )


def feasibility_for(plan: SitePlan, rule_id: str) -> FeasibilityVerdict | None:
    """Return the verdict of the concern the rule covers (its `concern_covered` check)."""
    detail = f"rule {rule_id!r}"
    concerns = {
        c.item
        for c in plan.acceptance.checks
        if c.check == "concern_covered" and c.detail == detail
    }
    return next((v for v in plan.feasibility if v.concern in concerns), None)


def spark_open_audit(plan: SitePlan) -> SparkOpenAudit:
    """Return what the Spark Open audit did (see the module docstring)."""
    if is_audit_skipped(plan):
        return "skipped"
    if plan.audit:
        return "findings"
    return "no_findings" if plan.back_translation else "not_run"


def worker_url(base_url: str, plan_id: str, camera_id: str, lang: str) -> str:
    """Return the worker page URL for a camera (`/worker/{site}/{camera}?lang=`, P2-11).

    Raises:
        CardError: If `base_url` is not an absolute http(s) URL.
    """
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        msg = f"base URL must be an absolute http(s) URL, got {base_url!r}"
        raise CardError(msg)
    path = f"/worker/{quote(plan_id, safe='')}/{quote(camera_id, safe='')}"
    return f"{base_url.rstrip('/')}{path}?lang={quote(lang, safe='')}"


def rule_card(
    plan: SitePlan, rule: RuleIR, *, lang: str | None = None, base_url: str | None = None
) -> RuleCardData:
    """Build one rule's card from the plan.

    Args:
        plan: The plan.
        rule: One of its rules.
        lang: The crew language; the plan's first language by default.
        base_url: Public base URL of the worker page; without it there is no QR code.

    Returns:
        The card's data.
    """
    lang = lang or plan_languages(plan)[0]
    summary = back_translation_for(plan, rule.id, lang)
    translated = summary is not None and not summary.startswith(NOT_BACK_TRANSLATED)
    cameras = tuple(dict.fromkeys(zone.camera_id for zone in rule.zones.values()))
    citations = _checks(plan, rule.id, "citation_text_match")
    verdict = feasibility_for(plan, rule.id)
    return RuleCardData(
        plan_id=plan.plan_id,
        plan_version=plan.version,
        site_name=plan.site_name,
        rule_id=rule.id,
        lang=lang,
        plain_language=summary if translated else None,
        not_back_translated=None if translated else summary or f"no [{lang}] back-translation",
        standards=rule.source.standard,
        citation_verified=citations[-1].passed if citations else None,
        feasibility=verdict.verdict if verdict is not None else None,
        rule_ci=rule_ci_numbers(plan, rule.id),
        calibrated_alpha=rule.verify.calibrated_alpha,
        spark_open=spark_open_audit(plan),
        audit_findings=0 if is_audit_skipped(plan) else len(plan.audit),
        cameras=cameras,
        worker_url=worker_url(base_url, plan.plan_id, cameras[0], lang)
        if base_url and cameras
        else None,
    )


def scrub(text: str, request: str) -> str:
    """Replace every word of `request` that is not policy vocabulary with `[redacted]`.

    Args:
        text: Model-written text.
        request: The declined request.

    Returns:
        `text` without the request's own words (names among them).
    """
    allowed = policy_words()
    banned = {w.casefold() for w in _WORD.findall(request)} - allowed
    return _WORD.sub(lambda m: REDACTED if m.group(0).casefold() in banned else m.group(0), text)


def declined_card(plan: SitePlan, verdict: FeasibilityVerdict) -> DeclinedCardData:
    """Build a declined concern's card: category, reason and alternative, never the request.

    Args:
        plan: The plan.
        verdict: One of its `declined` verdicts.

    Returns:
        The card's data.
    """
    reason = verdict.reason or ""
    curated = curated_decline(reason)
    if curated is not None:
        return DeclinedCardData(
            plan_id=plan.plan_id,
            site_name=plan.site_name,
            category=str(curated.category),
            reason=curated.reason,
            alternative=curated.alternative,
        )
    alternative = verdict.alternative
    return DeclinedCardData(
        plan_id=plan.plan_id,
        site_name=plan.site_name,
        category=None,
        reason=scrub(reason, verdict.concern),
        alternative=scrub(alternative, verdict.concern) if alternative else None,
    )


def plan_cards(
    plan: SitePlan, *, lang: str | None = None, base_url: str | None = None
) -> tuple[tuple[RuleCardData, ...], tuple[DeclinedCardData, ...]]:
    """Build every rule card and declined card of a plan, in plan order."""
    rules = tuple(rule_card(plan, r, lang=lang, base_url=base_url) for r in plan.rules)
    declined = tuple(declined_card(plan, v) for v in plan.feasibility if v.verdict == "declined")
    return rules, declined


def load_plan_cards(
    store: PlanStore, plan_id: str, *, lang: str | None = None, base_url: str | None = None
) -> tuple[tuple[RuleCardData, ...], tuple[DeclinedCardData, ...]]:
    """Load a saved plan and build its cards.

    Args:
        store: The plans directory.
        plan_id: The plan (validated as a safe file name by the store).
        lang: The crew language; the plan's first language by default.
        base_url: Public base URL of the worker page.

    Returns:
        Rule cards and declined cards.

    Raises:
        CardError: If there is no saved plan with that id.
        PipelineError: If the id is unsafe or the saved plan is invalid.
    """
    plan = store.load_plan(plan_id)
    if plan is None:
        msg = f"no saved plan {plan_id!r}"
        raise CardError(msg)
    return plan_cards(plan, lang=lang, base_url=base_url)
