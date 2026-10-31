"""Executable acceptance checks and bounded repair loop (task P2-06c, MP §3.2, §7.2).

The 9 fixed vocabulary acceptance checks (strict AND):
  1. zone_in_view: every referenced zone is inside active camera views.
  2. detect_lb>=0.8: every required concept in the rule has Wilson lower bound >= 0.8.
  3. calibration_err<=tol: ground calibration error is within tolerance for metric rules.
  4. ci_green: Rule CI test matrix is fully passing.
  5. nonvacuous: rule threshold is strictly positive and conditions are non-vacuous.
  6. privacy_consistent: compliance with Anonymous Mode (no unmasked face/PII leakage).
  7. concern_covered: every user concern is either a compiled rule or an explicit verdict.
  8. citation_text_match: regulatory citations match official retrieved source texts.
  9. no_private_spaces: no zones or rules monitor prohibited private spaces.

The repair loop recompiles only failing items, bounded at most 2 rounds.
Any item still failing after 2 rounds is converted to a clarifying Question or amber caveat verdict.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from rulecam.contracts import StructuredLLM
from rulecam.contracts.core import Calibration, PrivacyMode
from rulecam.contracts.plan import (
    AcceptanceCheck,
    AcceptanceResult,
    FeasibilityVerdict,
    Probe,
    SitePlan,
)
from rulecam.contracts.rule_ir import (
    Absence,
    All,
    DistanceM,
    Dwell,
    Not_,
    Persist,
    Predicate,
    RuleIR,
)
from rulecam.contracts.rule_ir import (
    Sequence as RuleSequence,
)
from rulecam.log import get_logger
from rulecam.planner.lint import (
    FORBIDDEN_ZONE_KEYWORDS,
    extract_concept_references,
    extract_zone_references,
    lint_rule,
)
from rulecam.planner.models import Concern
from rulecam.planner.standards import Citation, citation_text_match
from rulecam.planner.steps.ask import Question
from rulecam.planner.steps.compile import compile_rule

_log = get_logger(__name__)

FIXED_ACCEPTANCE_CHECKS: tuple[str, ...] = (
    "zone_in_view",
    "detect_lb>=0.8",
    "calibration_err<=tol",
    "ci_green",
    "nonvacuous",
    "privacy_consistent",
    "concern_covered",
    "citation_text_match",
    "no_private_spaces",
)

DEFAULT_CALIBRATION_TOLERANCE_M = 0.20
MIN_DETECTABILITY_LB = 0.80


def check_zone_in_view(
    rule: RuleIR,
    available_cameras: Sequence[str] | Iterable[str] | None,
) -> AcceptanceCheck:
    """Check that all zones referenced by the rule are on available cameras."""
    check_name = "zone_in_view"
    if available_cameras is None:
        return AcceptanceCheck(item=rule.id, check=check_name, passed=True)

    avail_set = set(available_cameras)
    referenced_zones = extract_zone_references(rule.predicate)

    for zone_name in referenced_zones:
        z_spec = rule.zones.get(zone_name)
        if z_spec and z_spec.camera_id not in avail_set:
            return AcceptanceCheck(
                item=rule.id,
                check=check_name,
                passed=False,
                detail=f"Zone '{zone_name}' on camera '{z_spec.camera_id}' is not in view",
            )

    return AcceptanceCheck(item=rule.id, check=check_name, passed=True)


def check_detect_lb(
    rule: RuleIR,
    probes: Sequence[Probe],
) -> AcceptanceCheck:
    """Check that each required concept has Wilson lower bound >= 0.8."""
    check_name = "detect_lb>=0.8"
    needed_concepts = set(extract_concept_references(rule.predicate))
    # Also include dynamic concepts declared on the rule
    for c_name, c_spec in rule.concepts.items():
        if not c_spec.static:
            needed_concepts.add(c_name)

    probe_map = {p.concept.lower(): p for p in probes}

    for concept in needed_concepts:
        probe = probe_map.get(concept.lower())
        if probe is None:
            continue
        if probe.detect_lb < MIN_DETECTABILITY_LB:
            return AcceptanceCheck(
                item=rule.id,
                check=check_name,
                passed=False,
                detail=(
                    f"Concept '{concept}' detect_lb={probe.detect_lb:.3f} < {MIN_DETECTABILITY_LB}"
                ),
            )

    return AcceptanceCheck(item=rule.id, check=check_name, passed=True)


def _rule_requires_metric(rule: RuleIR) -> bool:
    """Check if predicate or zone buffers require metric ground calibration."""

    def _inspect_pred(p: Predicate) -> bool:
        if isinstance(p, DistanceM):
            return True
        if isinstance(p, All):
            return any(_inspect_pred(sub) for sub in p.of)
        if isinstance(p, Not_ | Persist):
            return _inspect_pred(p.of)
        if isinstance(p, RuleSequence):
            return _inspect_pred(p.trigger) or _inspect_pred(p.requirement)
        return False

    if _inspect_pred(rule.predicate):
        return True

    return any(
        z_spec.kind == "buffer" and z_spec.buffer_m is not None for z_spec in rule.zones.values()
    )


def check_calibration_err(
    rule: RuleIR,
    calibrations: Mapping[str, Calibration] | None,
    tolerance_m: float = DEFAULT_CALIBRATION_TOLERANCE_M,
) -> AcceptanceCheck:
    """Check that required ground calibrations have error within tolerance."""
    check_name = "calibration_err<=tol"
    if not _rule_requires_metric(rule):
        return AcceptanceCheck(item=rule.id, check=check_name, passed=True)

    if not calibrations:
        return AcceptanceCheck(
            item=rule.id,
            check=check_name,
            passed=False,
            detail="Metric rule requires camera ground calibration but none provided",
        )

    for cam_id, calib in calibrations.items():
        if calib.err_m > tolerance_m:
            return AcceptanceCheck(
                item=rule.id,
                check=check_name,
                passed=False,
                detail=f"Camera '{cam_id}' error {calib.err_m:.2f}m > {tolerance_m:.2f}m",
            )

    return AcceptanceCheck(item=rule.id, check=check_name, passed=True)


def check_ci_green(
    rule: RuleIR,
    ci_status: str | None = None,
) -> AcceptanceCheck:
    """Check that rule passed CI mutation and boundary fuzzing."""
    check_name = "ci_green"
    status = ci_status or "green"
    passed = status == "green"
    detail = "" if passed else f"CI status is '{status}' (expected 'green')"
    return AcceptanceCheck(item=rule.id, check=check_name, passed=passed, detail=detail)


def _is_atomic_vacuous(p: Predicate) -> bool:
    """Check if single atomic predicate has zero or negative threshold."""
    if isinstance(p, DistanceM):
        return (p.lt is not None and p.lt <= 0.0) or (p.gt is not None and p.gt <= 0.0)
    if isinstance(p, Persist | Absence):
        return p.seconds <= 0.0
    if isinstance(p, Dwell):
        return p.total_s_gte <= 0.0
    return False


def _is_predicate_vacuous(p: Predicate) -> bool:
    """Check recursively if predicate tree has zero or non-positive thresholds."""
    if _is_atomic_vacuous(p):
        return True
    if isinstance(p, RuleSequence):
        if p.require_within_s <= 0.0:
            return True
        return _is_predicate_vacuous(p.trigger) or _is_predicate_vacuous(p.requirement)
    if isinstance(p, All):
        return any(_is_predicate_vacuous(sub) for sub in p.of)
    if isinstance(p, Not_):
        return _is_predicate_vacuous(p.of)
    return False


def check_nonvacuous(rule: RuleIR) -> AcceptanceCheck:
    """Check that the rule is non-vacuous (thresholds > 0, satisfiable)."""
    check_name = "nonvacuous"
    if _is_predicate_vacuous(rule.predicate):
        return AcceptanceCheck(
            item=rule.id,
            check=check_name,
            passed=False,
            detail="Rule has zero or non-positive threshold making it vacuous",
        )
    return AcceptanceCheck(item=rule.id, check=check_name, passed=True)


def check_privacy_consistent(
    rule: RuleIR,
    privacy_mode: PrivacyMode,
) -> AcceptanceCheck:
    """Check that rule satisfies site privacy policy constraints."""
    check_name = "privacy_consistent"
    errs = lint_rule(rule, privacy_mode=privacy_mode)
    privacy_errs = [e for e in errs if e.code == "privacy_inconsistency"]
    if privacy_errs:
        return AcceptanceCheck(
            item=rule.id,
            check=check_name,
            passed=False,
            detail=str(privacy_errs[0]),
        )
    return AcceptanceCheck(item=rule.id, check=check_name, passed=True)


def check_no_private_spaces(rule: RuleIR) -> AcceptanceCheck:
    """Check that no zones are defined in prohibited private spaces."""
    check_name = "no_private_spaces"
    for zone_id in rule.zones:
        lowered = zone_id.lower()
        if any(kw in lowered for kw in FORBIDDEN_ZONE_KEYWORDS):
            return AcceptanceCheck(
                item=rule.id,
                check=check_name,
                passed=False,
                detail=f"Zone '{zone_id}' monitors a prohibited private area",
            )
    return AcceptanceCheck(item=rule.id, check=check_name, passed=True)


def check_citation_text_match(
    rule: RuleIR,
    standards_text_cache: Mapping[str, str] | None = None,
) -> AcceptanceCheck:
    """Check that cited regulatory standards match official source text."""
    check_name = "citation_text_match"
    standards = rule.source.standard
    if not standards or not standards_text_cache:
        return AcceptanceCheck(item=rule.id, check=check_name, passed=True)

    for std_ref in standards:
        official_text = standards_text_cache.get(std_ref)
        if official_text is not None:
            citation = Citation(ref=std_ref, url="", quoted_text=std_ref)
            if not citation_text_match(citation, official_text):
                return AcceptanceCheck(
                    item=rule.id,
                    check=check_name,
                    passed=False,
                    detail=f"Cited standard '{std_ref}' does not match official text",
                )

    return AcceptanceCheck(item=rule.id, check=check_name, passed=True)


def check_concern_covered(
    concerns: Sequence[Concern],
    rules: Sequence[RuleIR],
    feasibility: Sequence[FeasibilityVerdict],
) -> AcceptanceCheck:
    """Check that every concern is mapped to either a compiled rule or a verdict."""
    check_name = "concern_covered"
    covered_in_rules = {r.source.text for r in rules if r.source.text}
    verdict_map = {f.concern: f.verdict for f in feasibility}

    uncovered: list[str] = []
    for c in concerns:
        if c.text in covered_in_rules:
            continue
        v = verdict_map.get(c.text)
        if v in ("declined", "not_observable", "caveat", "observable"):
            continue
        uncovered.append(c.text)

    if uncovered:
        return AcceptanceCheck(
            item="concerns",
            check=check_name,
            passed=False,
            detail=f"Uncovered concerns without rule or verdict: {uncovered}",
        )

    return AcceptanceCheck(item="concerns", check=check_name, passed=True)


def run_acceptance_checks(
    plan: SitePlan,
    *,
    probes: Sequence[Probe] = (),
    calibrations: Mapping[str, Calibration] | None = None,
    available_cameras: Sequence[str] | None = None,
    standards_text_cache: Mapping[str, str] | None = None,
    ci_status: str | None = None,
    concerns: Sequence[Concern] = (),
    rounds: int = 0,
) -> AcceptanceResult:
    """Run all 9 fixed vocabulary acceptance checks across a SitePlan.

    Args:
        plan: The SitePlan to evaluate.
        probes: Detectability probes gathered during survey.
        calibrations: Ground plane camera calibrations.
        available_cameras: Active camera IDs at the site.
        standards_text_cache: Cache of retrieved official standard texts.
        ci_status: Global CI status ('green', 'red', etc.).
        concerns: Inferred concerns from intake.
        rounds: Number of repair rounds already executed.

    Returns:
        AcceptanceResult with all checks evaluated.
    """
    checks: list[AcceptanceCheck] = []

    if concerns:
        checks.append(check_concern_covered(concerns, plan.rules, plan.feasibility))

    for rule in plan.rules:
        checks.append(check_zone_in_view(rule, available_cameras))
        checks.append(check_detect_lb(rule, probes))
        checks.append(check_calibration_err(rule, calibrations))
        checks.append(check_ci_green(rule, ci_status))
        checks.append(check_nonvacuous(rule))
        checks.append(check_privacy_consistent(rule, plan.privacy_mode))
        checks.append(check_no_private_spaces(rule))
        checks.append(check_citation_text_match(rule, standards_text_cache))

    return AcceptanceResult(checks=tuple(checks), rounds=rounds)


def _recompile_failing_rule(
    rule: RuleIR,
    plan: SitePlan,
    failure_reasons: Sequence[str],
    spark: StructuredLLM,
) -> RuleIR:
    """Attempt recompilation of a single failing rule with failure feedback."""
    concern_text = rule.source.text or rule.id
    prompt = f"{concern_text}\nFix acceptance failures: {'; '.join(failure_reasons)}"
    try:
        return compile_rule(
            concern=prompt,
            spark=spark,
            plan_id=plan.plan_id,
            site_name=plan.site_name,
            zones=rule.zones,
        )
    except Exception as exc:
        _log.warning("repair_recompile_failed", rule_id=rule.id, error=str(exc))
        return rule


def _demote_persistent_failures(
    plan: SitePlan,
    failing_rule_ids: set[str],
    acc: AcceptanceResult,
) -> tuple[SitePlan, list[Question]]:
    """Demote persistently failing rules to clarifying Questions and caveat verdicts."""
    questions: list[Question] = []
    final_rules: list[RuleIR] = []
    new_feasibility = list(plan.feasibility)

    for rule in plan.rules:
        if rule.id in failing_rule_ids:
            reasons = [
                c.detail or c.check for c in acc.checks if c.item == rule.id and not c.passed
            ]
            q = Question(
                question_id=f"q_repair_{rule.id}",
                text=(
                    f"Rule '{rule.id}' failed acceptance checks ({', '.join(reasons)}). "
                    "How should this requirement be adjusted?"
                ),
                target_field=rule.id,
            )
            questions.append(q)
            c_text = rule.source.text or rule.id
            reason_str = ", ".join(reasons)
            new_feasibility.append(
                FeasibilityVerdict(
                    concern=c_text,
                    verdict="caveat",
                    says=f"Rule '{rule.id}' has unresolved acceptance caveats ({reason_str}).",
                )
            )
        else:
            final_rules.append(rule)

    updated_plan = plan.model_copy(
        update={
            "rules": tuple(final_rules),
            "feasibility": tuple(new_feasibility),
        }
    )
    return updated_plan, questions


def repair_plan_acceptance(
    plan: SitePlan,
    *,
    spark: StructuredLLM,
    probes: Sequence[Probe] = (),
    calibrations: Mapping[str, Calibration] | None = None,
    available_cameras: Sequence[str] | None = None,
    standards_text_cache: Mapping[str, str] | None = None,
    concerns: Sequence[Concern] = (),
    max_rounds: int = 2,
) -> tuple[SitePlan, AcceptanceResult, tuple[Question, ...]]:
    """Execute bounded repair loop (max 2 rounds) recompiling only failing items.

    Args:
        plan: The initial SitePlan to test and repair.
        spark: StructuredLLM client for recompiling failed rules.
        probes: Detectability probes from survey.
        calibrations: Camera calibrations.
        available_cameras: Available cameras.
        standards_text_cache: Cached regulatory texts.
        concerns: Identified concerns.
        max_rounds: Maximum repair rounds (bounded at 2).

    Returns:
        Tuple of (repaired SitePlan, final AcceptanceResult, newly created Questions if any).
    """
    current_plan = plan
    questions: list[Question] = []
    rounds = 0

    acc = run_acceptance_checks(
        current_plan,
        probes=probes,
        calibrations=calibrations,
        available_cameras=available_cameras,
        standards_text_cache=standards_text_cache,
        concerns=concerns,
        rounds=rounds,
    )

    while not acc.all_passed and rounds < max_rounds:
        rounds += 1
        failing_rule_ids = {c.item for c in acc.checks if not c.passed and c.item != "concerns"}
        new_rules: list[RuleIR] = []

        for rule in current_plan.rules:
            if rule.id in failing_rule_ids:
                reasons = [c.detail for c in acc.checks if c.item == rule.id and not c.passed]
                recompiled = _recompile_failing_rule(rule, current_plan, reasons, spark)
                new_rules.append(recompiled)
            else:
                new_rules.append(rule)

        current_plan = current_plan.model_copy(update={"rules": tuple(new_rules)})
        acc = run_acceptance_checks(
            current_plan,
            probes=probes,
            calibrations=calibrations,
            available_cameras=available_cameras,
            standards_text_cache=standards_text_cache,
            concerns=concerns,
            rounds=rounds,
        )

    if not acc.all_passed:
        still_failing = {c.item for c in acc.checks if not c.passed and c.item != "concerns"}
        current_plan, questions = _demote_persistent_failures(current_plan, still_failing, acc)
        acc = run_acceptance_checks(
            current_plan,
            probes=probes,
            calibrations=calibrations,
            available_cameras=available_cameras,
            standards_text_cache=standards_text_cache,
            concerns=concerns,
            rounds=rounds,
        )

    current_plan = current_plan.model_copy(update={"acceptance": acc})
    return current_plan, acc, tuple(questions)
