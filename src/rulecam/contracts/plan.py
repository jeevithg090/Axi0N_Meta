"""The Site Plan produced by the Planner (task book §4.3, MP §3.2, §7.2)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from rulecam.contracts.core import Frozen, PrivacyMode
from rulecam.contracts.rule_ir import RuleIR


class RequestItem(Frozen):
    """One piece of the user's request (text, voice, PDF, photo or video)."""

    kind: Literal["text", "voice", "pdf", "photo", "video"]
    file: str | None = None
    lang: str | None = None
    text: str | None = None
    extracted: tuple[str, ...] = ()


class InventoryItem(Frozen):
    """A concept found in a camera during the survey."""

    concept: str
    camera_id: str
    count: int
    mean_confidence: float


class Probe(Frozen):
    """Detectability of one concept on one camera, measured on sampled frames."""

    concept: str
    camera_id: str
    n_frames: int
    detect_rate: float
    detect_lb: float  # Wilson 95% lower bound
    median_px_height: float


class FeasibilityVerdict(Frozen):
    """Whether a concern can be checked by camera here, with evidence."""

    concern: str
    verdict: Literal["observable", "caveat", "not_observable", "declined"]
    says: str  # first-person line, e.g. "I can't see a thermometer…"
    evidence: tuple[Probe, ...] = ()
    alternative: str | None = None
    reason: str | None = None  # required when declined


class AcceptanceCheck(Frozen):
    """One executable acceptance check on a plan item."""

    item: str  # rule id or plan field
    check: str  # from the fixed vocabulary in planner/acceptance.py
    passed: bool
    detail: str = ""


class AcceptanceResult(Frozen):
    """All acceptance checks plus how many repair rounds were used."""

    checks: tuple[AcceptanceCheck, ...]
    rounds: int = Field(ge=0, le=2)

    @property
    def all_passed(self) -> bool:
        """Return True when every check passed (strict AND)."""
        return all(c.passed for c in self.checks)


class AuditFinding(Frozen):
    """A finding from the independent plan audit."""

    by: Literal["spark-open"]
    text: str
    reasoning_ref: str | None = None


class SitePlan(Frozen):
    """The complete, versioned plan for a site."""

    plan_id: str
    version: int = 1
    site_name: str
    setting_inferred: str
    languages: tuple[str, ...]
    request: tuple[RequestItem, ...]
    inventory: tuple[InventoryItem, ...]
    feasibility: tuple[FeasibilityVerdict, ...]
    rules: tuple[RuleIR, ...]
    privacy_mode: PrivacyMode
    retention_days: int = 30
    protected_two_key: tuple[str, ...] = ()
    roles: dict[str, str] = Field(default_factory=dict)  # role -> channel ("web_push", …)
    acceptance: AcceptanceResult
    audit: tuple[AuditFinding, ...] = ()
    back_translation: str = ""
    standards: tuple[str, ...] = ()
    cost_usd_per_cam_hour: float | None = None
