"""Build the worker rules page for one camera of a plan (task P2-11, MP §3.5).

Rules come from the saved plan: Glimmer's back-translation (P2-07) in the requested language,
the same text as the rule cards (P2-10). Events and counts come from the evidence chain
(`worker.log`). An event card shows only a short code, its rule and a 15-minute bucket.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from rulecam.cards.build import back_translation_for
from rulecam.contracts import RuleIR, SitePlan
from rulecam.planner.scope import rule_scope
from rulecam.planner.steps.back_translate import NOT_BACK_TRANSLATED, plan_languages
from rulecam.worker.log import SiteLog, event_code
from rulecam.worker.models import DisputeOutcome, EventCard, WorkerPage, WorkerRule

BUCKET_MINUTES: Final = 15
MAX_EVENT_CARDS: Final = 50


def bucket_start(ts: datetime) -> datetime:
    """Floor `ts` to its 15-minute bucket (UTC)."""
    ts = ts.astimezone(UTC)
    return ts.replace(minute=ts.minute - ts.minute % BUCKET_MINUTES, second=0, microsecond=0)


def camera_rules(plan: SitePlan, camera_id: str) -> tuple[RuleIR, ...]:
    """Return the plan's rules that run on `camera_id`.

    That is the same scope the runtime uses (`planner.scope.rule_scope`): a zone on the camera,
    the camera its calibration names, or the feasibility evidence that put it there. A rule with
    no zone (a forklift distance rule, say) is on the page of every camera it runs on.
    """
    return tuple(rule for rule in plan.rules if camera_id in rule_scope(plan.feasibility, rule))


def page_language(plan: SitePlan, lang: str | None) -> str:
    """Return `lang` if the plan was back-translated into it, else the plan's first language."""
    languages = plan_languages(plan)
    return lang if lang in languages else languages[0]


def plain_language(plan: SitePlan, rule_id: str, lang: str) -> str | None:
    """Return the rule's back-translation in `lang`, or `None` if there is none."""
    summary = back_translation_for(plan, rule_id, lang)
    if summary is None or summary.startswith(NOT_BACK_TRANSLATED):
        return None
    return summary


def build_page(plan: SitePlan, camera_id: str, lang: str | None, log: SiteLog) -> WorkerPage:
    """Build the page payload.

    Args:
        plan: The site plan.
        camera_id: A camera that at least one of the plan's rules watches.
        lang: The requested language (the plan's first language when absent or unknown).
        log: The site's events and disputes within the plan's retention window.

    Returns:
        Rules, never list, retention, anonymous event cards (newest first) and counts per rule.
    """
    lang = page_language(plan, lang)
    rules = camera_rules(plan, camera_id)
    rule_ids = {rule.id for rule in rules}
    events = sorted(
        (
            e.candidate
            for e in log.events.values()
            if e.candidate.camera_id == camera_id and e.candidate.rule_id in rule_ids
        ),
        key=lambda c: (c.ts_start, c.event_id),
        reverse=True,
    )
    cards = tuple(
        EventCard(
            code=event_code(c.event_id),
            rule_id=c.rule_id,
            bucket_start=bucket_start(c.ts_start),
            dispute=_latest_outcome(log, c.event_id),
        )
        for c in events[:MAX_EVENT_CARDS]
    )
    return WorkerPage(
        plan_id=plan.plan_id,
        site_name=plan.site_name,
        camera_id=camera_id,
        lang=lang,
        languages=plan_languages(plan),
        privacy_mode=plan.privacy_mode,
        retention_days=plan.retention_days,
        rules=tuple(
            WorkerRule(rule_id=r.id, plain_language=plain_language(plan, r.id, lang)) for r in rules
        ),
        events=cards,
        counts={r.id: log.counts(camera_id, r.id) for r in rules},
    )


def _latest_outcome(log: SiteLog, event_id: str) -> DisputeOutcome | None:
    disputes = log.disputes_of(event_id)
    return disputes[-1].outcome if disputes else None
