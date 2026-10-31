"""Plan and Rule IR linter (task P2-06b, MP §3.2, §7.2, V2 §6).

Inspects proposed SitePlans and RuleIR definitions for 8 deterministic error classes:
  1. undefined_concept: references concepts not declared in rule or plan inventory.
  2. undefined_zone: references zones not declared in rule or plan zones.
  3. zone_out_of_view: references zones belonging to cameras outside available views.
  4. unreasonable_threshold: ground distances outside [0.3, 20.0]m or durations outside [1, 3600]s.
  5. contradictory_rules: rules with same subject and zone having directly conflicting conditions.
  6. privacy_inconsistency: Anonymous/Strict privacy mode with facial/unmasked recognition.
  7. private_space_zone: zones located in private spaces (restroom, break room, locker room).
  8. missing_action_or_severity: empty actions list or missing severity.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from rulecam.contracts.core import Frozen, PrivacyMode
from rulecam.contracts.plan import SitePlan
from rulecam.contracts.rule_ir import (
    Absence,
    All,
    Any_,
    DistanceM,
    Dwell,
    Inside,
    Moving,
    Not_,
    Overlap,
    Persist,
    PoseState,
    Predicate,
    Rate,
    RuleIR,
    StateCheck,
    Without,
    ZoneSpec,
)
from rulecam.contracts.rule_ir import (
    Sequence as RuleSequence,
)
from rulecam.planner.errors import PlannerError

# Deterministic threshold bounds (MP §3.2, §7.2)
MIN_DISTANCE_M = 0.3
MAX_DISTANCE_M = 20.0
MIN_DURATION_S = 1.0
MAX_DURATION_S = 3600.0

FORBIDDEN_ZONE_KEYWORDS: tuple[str, ...] = (
    "bathroom",
    "restroom",
    "toilet",
    "washroom",
    "locker",
    "break room",
    "breakroom",
    "changing room",
    "shower",
    "bedroom",
)

FACE_OR_IDENTITY_KEYWORDS: tuple[str, ...] = (
    "face",
    "facial",
    "identity",
    "individual",
    "person_id",
    "unmasked_face",
    "retina",
    "biometric",
)


class LintFinding(Frozen):
    """Structured report of a single lint finding."""

    code: str
    rule_id: str | None
    message: str
    field: str | None = None


class LintError(PlannerError):
    """Base exception for all plan and rule lint errors."""

    def __init__(self, code: str, rule_id: str | None, message: str) -> None:
        """Initialise LintError with code, rule_id, and descriptive message."""
        self.code = code
        self.rule_id = rule_id
        super().__init__(f"[{code}] (rule: {rule_id}): {message}")


class UndefinedConceptError(LintError):
    """Raised when a rule references an undeclared concept."""

    def __init__(self, rule_id: str | None, concept: str) -> None:
        """Initialise UndefinedConceptError."""
        super().__init__("undefined_concept", rule_id, f"Undefined concept reference: '{concept}'")


class UndefinedZoneError(LintError):
    """Raised when a rule references an undeclared zone."""

    def __init__(self, rule_id: str | None, zone: str) -> None:
        """Initialise UndefinedZoneError."""
        super().__init__("undefined_zone", rule_id, f"Undefined zone reference: '{zone}'")


class ZoneOutOfViewError(LintError):
    """Raised when a zone is on a camera outside available views."""

    def __init__(self, rule_id: str | None, zone: str, camera_id: str) -> None:
        """Initialise ZoneOutOfViewError."""
        super().__init__(
            "zone_out_of_view",
            rule_id,
            f"Zone '{zone}' camera '{camera_id}' is not in available camera views",
        )


class UnreasonableThresholdError(LintError):
    """Raised when numeric thresholds fall outside physical or operational limits."""

    def __init__(self, rule_id: str | None, detail: str) -> None:
        """Initialise UnreasonableThresholdError."""
        super().__init__("unreasonable_threshold", rule_id, detail)


class ContradictoryRulesError(LintError):
    """Raised when two rules or clauses have directly contradictory requirements."""

    def __init__(self, rule_id: str | None, detail: str) -> None:
        """Initialise ContradictoryRulesError."""
        super().__init__("contradictory_rules", rule_id, detail)


class PrivacyInconsistencyError(LintError):
    """Raised when Anonymous Mode is used with rules requiring facial identification."""

    def __init__(self, rule_id: str | None, detail: str) -> None:
        """Initialise PrivacyInconsistencyError."""
        super().__init__("privacy_inconsistency", rule_id, detail)


class PrivateSpaceZoneError(LintError):
    """Raised when a zone is designated in a strictly private area."""

    def __init__(self, rule_id: str | None, zone_name: str) -> None:
        """Initialise PrivateSpaceZoneError."""
        super().__init__(
            "private_space_zone",
            rule_id,
            f"Zone '{zone_name}' is inside a prohibited private space",
        )


class MissingActionError(LintError):
    """Raised when a rule defines no actions."""

    def __init__(self, rule_id: str | None) -> None:
        """Initialise MissingActionError."""
        super().__init__("missing_action", rule_id, "Rule must declare at least one action")


class MissingSeverityError(LintError):
    """Raised when a rule is missing a severity rating."""

    def __init__(self, rule_id: str | None) -> None:
        """Initialise MissingSeverityError."""
        super().__init__("missing_severity", rule_id, "Rule must declare a valid severity")


def _extract_direct_concepts(pred: Predicate) -> set[str]:
    """Extract root concepts from atomic predicates."""
    refs: set[str] = set()
    if isinstance(pred, DistanceM | Overlap):
        refs.add(pred.a.split(".")[0])
        refs.add(pred.b.split(".")[0])
    elif isinstance(pred, Without):
        refs.add(pred.subject.split(".")[0])
        refs.add(pred.item.split(".")[0])
    elif isinstance(pred, Inside | Moving | Dwell | Absence | PoseState | StateCheck):
        refs.add(pred.object.split(".")[0])
    return refs


def extract_concept_references(pred: Predicate) -> set[str]:
    """Recursively extract all root concept names referenced in a predicate."""
    refs = _extract_direct_concepts(pred)

    if isinstance(pred, All | Any_):
        for sub in pred.of:
            refs.update(extract_concept_references(sub))
    elif isinstance(pred, Not_ | Persist | Rate):
        refs.update(extract_concept_references(pred.of))
    elif isinstance(pred, RuleSequence):
        refs.update(extract_concept_references(pred.trigger))
        refs.update(extract_concept_references(pred.requirement))

    return refs


def extract_zone_references(pred: Predicate) -> set[str]:
    """Recursively extract all zone names referenced in a predicate."""
    zones: set[str] = set()

    if isinstance(pred, Inside | Dwell | Absence):
        zones.add(pred.zone)
    elif isinstance(pred, All | Any_):
        for sub in pred.of:
            zones.update(extract_zone_references(sub))
    elif isinstance(pred, Not_ | Persist | Rate):
        zones.update(extract_zone_references(pred.of))
    elif isinstance(pred, RuleSequence):
        zones.update(extract_zone_references(pred.trigger))
        zones.update(extract_zone_references(pred.requirement))

    return zones


def _check_distance_thresholds(rule_id: str | None, pred: DistanceM) -> list[LintError]:
    errors: list[LintError] = []
    for val, name in ((pred.lt, "lt"), (pred.gt, "gt")):
        if val is not None and (val < MIN_DISTANCE_M or val > MAX_DISTANCE_M):
            errors.append(
                UnreasonableThresholdError(
                    rule_id,
                    f"Distance threshold {name}={val}m is outside valid range "
                    f"[{MIN_DISTANCE_M}, {MAX_DISTANCE_M}]m",
                )
            )
    return errors


def _check_temporal_thresholds(rule_id: str | None, pred: Predicate) -> list[LintError]:
    errors: list[LintError] = []
    if isinstance(pred, Persist) and (
        pred.seconds < MIN_DURATION_S or pred.seconds > MAX_DURATION_S
    ):
        errors.append(
            UnreasonableThresholdError(
                rule_id,
                f"Persist duration {pred.seconds}s is outside valid range "
                f"[{MIN_DURATION_S}, {MAX_DURATION_S}]s",
            )
        )
    elif isinstance(pred, Dwell):
        if pred.total_s_gte < MIN_DURATION_S or pred.total_s_gte > MAX_DURATION_S:
            errors.append(
                UnreasonableThresholdError(
                    rule_id,
                    f"Dwell duration {pred.total_s_gte}s is outside valid range "
                    f"[{MIN_DURATION_S}, {MAX_DURATION_S}]s",
                )
            )
        if pred.max_gap_s < 0.0 or pred.max_gap_s > MAX_DURATION_S:
            errors.append(
                UnreasonableThresholdError(
                    rule_id,
                    f"Dwell max_gap_s {pred.max_gap_s}s is negative or exceeds {MAX_DURATION_S}s",
                )
            )
    elif isinstance(pred, Absence) and (
        pred.seconds < MIN_DURATION_S or pred.seconds > MAX_DURATION_S
    ):
        errors.append(
            UnreasonableThresholdError(
                rule_id,
                f"Absence duration {pred.seconds}s is outside valid range "
                f"[{MIN_DURATION_S}, {MAX_DURATION_S}]s",
            )
        )
    elif isinstance(pred, RuleSequence) and (
        pred.require_within_s < MIN_DURATION_S or pred.require_within_s > MAX_DURATION_S
    ):
        errors.append(
            UnreasonableThresholdError(
                rule_id,
                f"Sequence require_within_s {pred.require_within_s}s is outside valid range "
                f"[{MIN_DURATION_S}, {MAX_DURATION_S}]s",
            )
        )
    return errors


def _check_thresholds(rule: RuleIR, pred: Predicate) -> list[LintError]:
    """Recursively validate numeric distance and duration thresholds."""
    errors: list[LintError] = []

    if isinstance(pred, DistanceM):
        errors.extend(_check_distance_thresholds(rule.id, pred))
    else:
        errors.extend(_check_temporal_thresholds(rule.id, pred))

    if isinstance(pred, All | Any_):
        for sub in pred.of:
            errors.extend(_check_thresholds(rule, sub))
    elif isinstance(pred, Not_ | Persist | Rate):
        errors.extend(_check_thresholds(rule, pred.of))
    elif isinstance(pred, RuleSequence):
        errors.extend(_check_thresholds(rule, pred.trigger))
        errors.extend(_check_thresholds(rule, pred.requirement))

    return errors


def _check_predicate_self_contradiction(rule: RuleIR, pred: Predicate) -> list[LintError]:
    """Check for self-contradictory clauses inside a single rule predicate."""
    errors: list[LintError] = []
    if isinstance(pred, All):
        positive_inside: set[tuple[str, str]] = set()
        negative_inside: set[tuple[str, str]] = set()

        for clause in pred.of:
            if isinstance(clause, Inside):
                positive_inside.add((clause.object, clause.zone))
            elif isinstance(clause, Not_) and isinstance(clause.of, Inside):
                negative_inside.add((clause.of.object, clause.of.zone))

        overlap = positive_inside & negative_inside
        if overlap:
            obj, zone = next(iter(overlap))
            errors.append(
                ContradictoryRulesError(
                    rule.id,
                    f"Predicate contains contradictory clauses: {obj} is both inside and "
                    f"not inside zone {zone}",
                )
            )

    return errors


def _check_inter_rule_contradictions(rules: Sequence[RuleIR]) -> list[LintError]:
    """Check for contradictory requirements between multiple rules."""
    errors: list[LintError] = []

    inside_rules: dict[tuple[str, str], list[str]] = {}
    not_inside_rules: dict[tuple[str, str], list[str]] = {}

    for rule in rules:
        pred = rule.predicate
        if isinstance(pred, Inside):
            key = (pred.object, pred.zone)
            inside_rules.setdefault(key, []).append(rule.id)
        elif isinstance(pred, Not_) and isinstance(pred.of, Inside):
            key = (pred.of.object, pred.of.zone)
            not_inside_rules.setdefault(key, []).append(rule.id)

    for key, r_inside in inside_rules.items():
        if key in not_inside_rules:
            r_outside = not_inside_rules[key]
            errors.append(
                ContradictoryRulesError(
                    r_inside[0],
                    f"Rules {r_inside} and {r_outside} contradict each other: "
                    f"one requires {key[0]} inside {key[1]} while the other requires outside",
                )
            )

    return errors


def _check_rule_actions_and_severity(rule: RuleIR) -> list[LintError]:
    errors: list[LintError] = []
    if not rule.actions:
        errors.append(MissingActionError(rule.id))
    if not rule.severity:
        errors.append(MissingSeverityError(rule.id))
    return errors


def _check_rule_zones(
    rule: RuleIR,
    all_zones: Mapping[str, ZoneSpec],
    available_cameras: Sequence[str] | Iterable[str] | None,
) -> list[LintError]:
    errors: list[LintError] = []
    for zone_id in all_zones:
        lowered = zone_id.lower()
        if any(kw in lowered for kw in FORBIDDEN_ZONE_KEYWORDS):
            errors.append(PrivateSpaceZoneError(rule.id, zone_id))

    referenced_zones = extract_zone_references(rule.predicate)
    for zone_name in referenced_zones:
        if zone_name not in all_zones:
            errors.append(UndefinedZoneError(rule.id, zone_name))

    if available_cameras is not None:
        avail_cam_set = set(available_cameras)
        for zone_name in referenced_zones:
            z_spec = all_zones.get(zone_name)
            if z_spec and z_spec.camera_id not in avail_cam_set:
                errors.append(ZoneOutOfViewError(rule.id, zone_name, z_spec.camera_id))

    return errors


def _check_rule_concepts(
    rule: RuleIR,
    inventory_concepts: Sequence[str] | Iterable[str] | None,
) -> list[LintError]:
    errors: list[LintError] = []
    referenced_concepts = extract_concept_references(rule.predicate)
    declared_concepts = set(rule.concepts.keys())
    if inventory_concepts:
        declared_concepts.update(inventory_concepts)

    for concept in referenced_concepts:
        if concept not in declared_concepts:
            errors.append(UndefinedConceptError(rule.id, concept))
    return errors


def _check_rule_privacy(rule: RuleIR, privacy_mode: PrivacyMode) -> list[LintError]:
    errors: list[LintError] = []
    if privacy_mode in (PrivacyMode.ANONYMOUS, PrivacyMode.STRICT):
        for concept_name in rule.concepts:
            lowered = concept_name.lower()
            if any(kw in lowered for kw in FACE_OR_IDENTITY_KEYWORDS):
                errors.append(
                    PrivacyInconsistencyError(
                        rule.id,
                        f"Concept '{concept_name}' requires facial/identity recognition, "
                        f"which violates {privacy_mode.value.title()} Privacy Mode",
                    )
                )
    return errors


def lint_rule(
    rule: RuleIR,
    *,
    privacy_mode: PrivacyMode = PrivacyMode.STANDARD,
    known_zones: Mapping[str, ZoneSpec] | None = None,
    available_cameras: Sequence[str] | Iterable[str] | None = None,
    inventory_concepts: Sequence[str] | Iterable[str] | None = None,
) -> list[LintError]:
    """Validate a single RuleIR for the 8 deterministic lint error classes.

    Args:
        rule: The RuleIR instance to check.
        privacy_mode: Operational privacy mode for this deployment.
        known_zones: Mapping of zone_id to ZoneSpec declared in plan or rule.
        available_cameras: Optional collection of active camera IDs.
        inventory_concepts: Optional concepts verified in survey inventory.

    Returns:
        List of LintError instances discovered.
    """
    errors: list[LintError] = []
    errors.extend(_check_rule_actions_and_severity(rule))

    all_zones: dict[str, ZoneSpec] = dict(rule.zones)
    if known_zones:
        all_zones.update(known_zones)

    errors.extend(_check_rule_zones(rule, all_zones, available_cameras))
    errors.extend(_check_rule_concepts(rule, inventory_concepts))
    errors.extend(_check_thresholds(rule, rule.predicate))
    errors.extend(_check_rule_privacy(rule, privacy_mode))
    errors.extend(_check_predicate_self_contradiction(rule, rule.predicate))

    return errors


def lint_plan(
    plan: SitePlan,
    *,
    available_cameras: Sequence[str] | Iterable[str] | None = None,
) -> list[LintError]:
    """Validate all rules and configurations in a SitePlan.

    Args:
        plan: The SitePlan to validate.
        available_cameras: Optional active camera IDs at the site.

    Returns:
        List of all LintErrors discovered across rules and global plan configuration.
    """
    errors: list[LintError] = []

    known_zones: dict[str, ZoneSpec] = {}
    for r in plan.rules:
        known_zones.update(r.zones)

    inventory_concepts = [item.concept for item in plan.inventory]
    cams = (
        list(available_cameras)
        if available_cameras is not None
        else list({item.camera_id for item in plan.inventory})
    )

    for rule in plan.rules:
        errors.extend(
            lint_rule(
                rule,
                privacy_mode=plan.privacy_mode,
                known_zones=known_zones,
                available_cameras=cams,
                inventory_concepts=inventory_concepts,
            )
        )

    errors.extend(_check_inter_rule_contradictions(plan.rules))

    return errors


def lint_plan_or_raise(
    plan: SitePlan,
    *,
    available_cameras: Sequence[str] | Iterable[str] | None = None,
) -> None:
    """Validate a SitePlan and raise the first LintError if any are found."""
    errors = lint_plan(plan, available_cameras=available_cameras)
    if errors:
        raise errors[0]
