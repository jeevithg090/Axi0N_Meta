"""Data models for visual Rule Cards and Declined Cards (task P2-10, MP §12)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RuleCardData(BaseModel):
    """Data required to render an active, verified Rule Card."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    plain_language: str
    language: str = "en"
    cited_standard: str
    feasibility_verdict: Literal["green", "amber", "red"] = "green"
    boundary_traces_passed: int = 3
    boundary_traces_total: int = 3
    mutants_killed: int = 5
    mutants_total: int = 5
    lookalikes_tested: int = 1
    lookalikes_fooled: int = 0
    calibrated_false_alarm_rate: float | None = None
    verified_by: str = "Glimmer + Spark Open"
    skeleton_thumbnail_b64: str | None = None
    qr_code_url: str = "https://rulecam.site/verify"
    site_id: str = "site-main"
    camera_id: str = "cam1"


class DeclinedCardData(BaseModel):
    """Data for rendering an ethically declined request card.

    Strict Privacy Guardrail:
    Never accepts or includes raw request text or names.
    Only category, policy reason, and station-level alternative are permitted.
    """

    model_config = ConfigDict(extra="forbid")

    category: str
    reason: str
    station_alternative: str
    site_id: str = "site-main"
    policy_reference: str = "Rulecam Responsible AI & Privacy Standard (SA §5, MP §3.6)"
