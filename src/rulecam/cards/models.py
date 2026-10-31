"""What a rule card and a declined card show, all read from a saved plan (task P2-10)."""

from __future__ import annotations

from typing import Literal

from rulecam.contracts.core import Frozen

FeasibilityLabel = Literal["observable", "caveat", "not_observable", "declined"]
SparkOpenAudit = Literal["findings", "no_findings", "skipped", "not_run"]


class LookalikeNumbers(Frozen):
    """The lookalike red-team's numbers (`LookalikeReport.summary()`)."""

    tested: int
    fooled: int


class RuleCINumbers(Frozen):
    """A rule's Rule CI numbers, from its `ci_green` check in the acceptance trace.

    Attributes:
        boundary_passed: Boundary traces whose outcome matched.
        boundary_total: Boundary traces run.
        mutants_killed: Mutants killed.
        mutants_total: Mutants run.
        lookalikes: The lookalike red-team, or `None` when it did not run.
        ci_green: The `ci_green` check's verdict.
    """

    boundary_passed: int
    boundary_total: int
    mutants_killed: int
    mutants_total: int
    lookalikes: LookalikeNumbers | None = None
    ci_green: bool


class RuleCardData(Frozen):
    """Everything a rule card shows; `None` means the plan does not record it.

    Attributes:
        plan_id: The plan.
        plan_version: The plan's version.
        site_name: The site.
        rule_id: The rule.
        lang: The language of `plain_language` (the crew's).
        plain_language: Glimmer's back-translation of the rule's IR in `lang`.
        not_back_translated: Why there is no back-translation, when there is none.
        standards: The rule's cited standards.
        citation_verified: The `citation_text_match` check, if it ran.
        feasibility: The verdict of the concern the rule covers.
        rule_ci: Rule CI numbers, if Rule CI ran.
        calibrated_alpha: The rule's calibrated false-alarm rate (`VerifySpec`), if set.
        spark_open: What the Spark Open plan audit did.
        audit_findings: How many findings the audit made (plan-wide).
        cameras: Cameras the rule's zones are on.
        worker_url: The worker page the QR code opens, if a public base URL is known.
    """

    plan_id: str
    plan_version: int
    site_name: str
    rule_id: str
    lang: str
    plain_language: str | None
    not_back_translated: str | None
    standards: tuple[str, ...]
    citation_verified: bool | None
    feasibility: FeasibilityLabel | None
    rule_ci: RuleCINumbers | None
    calibrated_alpha: float | None
    spark_open: SparkOpenAudit
    audit_findings: int
    cameras: tuple[str, ...]
    worker_url: str | None

    @property
    def glimmer_checked(self) -> bool:
        """Glimmer back-translated the rule."""
        return self.plain_language is not None


class DeclinedCardData(Frozen):
    """A declined concern: category, reason and alternative, never the request or a name.

    Attributes:
        plan_id: The plan.
        site_name: The site.
        category: The guardrail category, or `None` when the plan does not record it (a
            model decline keeps its reason but not its category).
        reason: Why it was declined, with every request word scrubbed from model text.
        alternative: The privacy-preserving alternative, scrubbed the same way.
    """

    plan_id: str
    site_name: str
    category: str | None
    reason: str
    alternative: str | None
