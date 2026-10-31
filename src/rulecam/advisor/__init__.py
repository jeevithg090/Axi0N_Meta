"""Site Advisor: process log, metrics, reasoning pipeline, statistics and what-ifs (SA §4)."""

from __future__ import annotations

from rulecam.advisor.claims import (
    assert_template_has_no_digits,
    build_slot_claim,
    lint_claim_text,
    render_slot_claim,
)
from rulecam.advisor.pipeline import (
    AdvisorQuery,
    DeclinedQuestionResult,
    RecordedWindow,
    SiteAdvisorPipeline,
    WindowGuardResult,
)
from rulecam.advisor.rank import calculate_rank_score
from rulecam.advisor.safety import check_safety_veto

__all__ = [
    "AdvisorQuery",
    "DeclinedQuestionResult",
    "RecordedWindow",
    "SiteAdvisorPipeline",
    "WindowGuardResult",
    "assert_template_has_no_digits",
    "build_slot_claim",
    "calculate_rank_score",
    "check_safety_veto",
    "lint_claim_text",
    "render_slot_claim",
]
