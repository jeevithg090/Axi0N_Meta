"""The plan linter: step 7 of the Site Planner (task P2-06b, MP §3.2).

Deterministic checks only (no model calls) on the compiled rules of a `SitePlan`. Each finding
has one of these codes:

- `undefined_concept`: a predicate names a concept the rule does not declare;
- `undefined_zone`: a predicate names a zone the rule does not declare;
- `zone_out_of_view`: a zone is on a camera the site does not have, or its polygon lies outside
  that camera's frame;
- `unreasonable_threshold`: a distance outside 0.3-20 m or a duration outside 1-3,600 s;
- `contradictory_rules`: the same subject and zone with opposite conditions (inside vs. not
  inside or absent), within one rule's conjunction or across two rules;
- `privacy_inconsistency`: Anonymous or Strict Mode with a concept that needs faces;
- `private_space_zone`: a zone named after a private space (bathroom, break room, ...);
- `missing_action`: a rule with no actions;
- `missing_severity`: a rule without a valid severity.

`lint_plan` returns the findings; `require_lint_clean` raises `PlanLintError` with all of them.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Final, Literal

from rulecam.contracts.core import Frozen, PrivacyMode, Severity, Zone
from rulecam.contracts.plan import SitePlan
from rulecam.contracts.rule_ir import (
    Absence,
    All,
    Count,
    DistanceM,
    Dwell,
    Inside,
    Not_,
    Persist,
    Predicate,
    RuleIR,
    ZoneSpec,
)
from rulecam.contracts.rule_ir import (
    Sequence as RuleSequence,
)
from rulecam.perception.calibration import zone_in_view
from rulecam.perception.errors import ZoneError
from rulecam.planner.errors import PlannerError
from rulecam.planner.guardrails import GuardrailCategory, deterministic_check
from rulecam.planner.steps.compile import walk_predicates
from rulecam.prototype import prototype_mode

MIN_DISTANCE_M: Final = 0.3
MAX_DISTANCE_M: Final = 20.0
MIN_DURATION_S: Final = 1.0
MAX_DURATION_S: Final = 3600.0
PRIVATE_MODES: Final = frozenset({PrivacyMode.ANONYMOUS, PrivacyMode.STRICT})
FACE_WORDS: Final = re.compile(r"\b(?:faces?|facial|head\s?shots?|iris|irises|retinas?)\b")
_OBJECT_FIELDS: Final = ("a", "b", "object", "subject", "item")

LintCode = Literal[
    "undefined_concept",
    "undefined_zone",
    "zone_out_of_view",
    "unreasonable_threshold",
    "contradictory_rules",
    "privacy_inconsistency",
    "private_space_zone",
    "missing_action",
    "missing_severity",
]
FrameSize = tuple[int, int]
CameraViews = Mapping[str, FrameSize | None]
"""Camera id -> its frame size (width, height) in pixels, `None` when no frame could be read."""


class LintFinding(Frozen):
    """One lint error.

    Attributes:
        code: The error class.
        rule_id: The rule it is about.
        message: What is wrong, for the owner and the repair prompt.
    """

    code: LintCode
    rule_id: str
    message: str


class PlanLintError(PlannerError):
    """A plan has lint errors.

    Attributes:
        findings: Every finding, in rule order.
    """

    def __init__(self, findings: Sequence[LintFinding]) -> None:
        """Create the error.

        Args:
            findings: The findings (at least one).
        """
        self.findings = tuple(findings)
        listed = "; ".join(f"[{f.code}] {f.rule_id}: {f.message}" for f in self.findings)
        super().__init__(f"{len(self.findings)} lint error(s): {listed}")


def concept_root(ref: str) -> str:
    """The concept name of an object reference (`person.hands` -> `person`)."""
    return ref.split(".", 1)[0]


def private_space_zones(rule: RuleIR) -> list[str]:
    """Names of the rule's zones that name a private space (the guardrails' patterns)."""
    return [name for name in rule.zones if _names_private_space(name)]


def face_concepts(rule: RuleIR) -> list[str]:
    """Names of the rule's concepts whose name or prompt needs a face."""
    return [
        name
        for name, spec in rule.concepts.items()
        if FACE_WORDS.search(_words(name)) or FACE_WORDS.search(_words(spec.prompt))
    ]


def zone_view_problem(name: str, spec: ZoneSpec, views: CameraViews) -> str | None:
    """Why a zone is out of view, or `None` when it is not known to be.

    A zone is out of view when its camera is not one of the site's, or when its pixel polygon
    shares no area with (or is not a valid polygon in) the camera's frame. Buffer zones follow
    their concept, so only their camera is checked.

    Args:
        name: The zone's name.
        spec: The zone.
        views: The site's cameras and frame sizes.

    Returns:
        The problem, or `None`.
    """
    if spec.camera_id not in views:
        return f"zone {name!r} is on camera {spec.camera_id!r}, which the site does not have"
    size = views[spec.camera_id]
    if spec.kind != "polygon" or size is None:
        return None
    zone = Zone(zone_id=name, camera_id=spec.camera_id, kind="polygon", polygon_px=spec.polygon_px)
    try:
        view = zone_in_view(zone, *size)
    except ZoneError as exc:
        return f"zone {name!r} has no usable polygon: {exc}"
    if view.state == "outside":
        return f"zone {name!r} lies outside the {size[0]}x{size[1]} frame of {spec.camera_id!r}"
    return None


def lint_rule(
    rule: RuleIR, *, views: CameraViews, privacy_mode: PrivacyMode = PrivacyMode.STANDARD
) -> tuple[LintFinding, ...]:
    """Lint one rule on its own (everything but cross-rule contradictions).

    Args:
        rule: The rule.
        views: The site's cameras and frame sizes.
        privacy_mode: The site's privacy mode.

    Returns:
        The findings, empty when the rule is clean.
    """
    problems: list[tuple[LintCode, str]] = [
        *_reference_problems(rule),
        *(
            ("zone_out_of_view", problem)
            for name, spec in rule.zones.items()
            if (problem := zone_view_problem(name, spec, views)) is not None
        ),
        *(("unreasonable_threshold", p) for p in _threshold_problems(rule)),
        *(("contradictory_rules", p) for p in _self_contradictions(rule)),
        *_privacy_problems(rule, privacy_mode),
        *_action_problems(rule),
    ]
    return tuple(LintFinding(code=code, rule_id=rule.id, message=m) for code, m in problems)


def lint_plan(plan: SitePlan, *, views: CameraViews) -> tuple[LintFinding, ...]:
    """Lint every rule of a plan, then look for contradictions between rules.

    Args:
        plan: The plan.
        views: The site's cameras and frame sizes.

    Returns:
        The findings, empty when the plan is clean.
    """
    findings = [
        finding
        for rule in plan.rules
        for finding in lint_rule(rule, views=views, privacy_mode=plan.privacy_mode)
    ]
    findings.extend(cross_rule_contradictions(plan.rules))
    return tuple(findings)


def require_lint_clean(plan: SitePlan, *, views: CameraViews) -> None:
    """Raise `PlanLintError` if the plan has any lint error.

    Args:
        plan: The plan.
        views: The site's cameras and frame sizes.

    Raises:
        PlanLintError: With every finding.
    """
    findings = lint_plan(plan, views=views)
    if findings:
        raise PlanLintError(findings)


def cross_rule_contradictions(rules: Sequence[RuleIR]) -> list[LintFinding]:
    """Pairs of rules that require the same subject both in and out of the same zone.

    Args:
        rules: The plan's rules.

    Returns:
        One finding per contradicting pair, on the later rule.
    """
    findings: list[LintFinding] = []
    for i, later in enumerate(rules):
        for earlier in rules[:i]:
            flipped = {(subj, zone, not on) for subj, zone, on in _literals(later.predicate)}
            clash = _literals(earlier.predicate) & flipped
            if clash:
                subject, zone, _ = min(clash)
                message = (
                    f"rules {earlier.id!r} and {later.id!r} have opposite conditions for "
                    f"{subject!r} in zone {zone!r}"
                )
                findings.append(
                    LintFinding(code="contradictory_rules", rule_id=later.id, message=message)
                )
    return findings


# --- checks -------------------------------------------------------------------------------------


def _reference_problems(rule: RuleIR) -> Iterator[tuple[LintCode, str]]:
    for pred in walk_predicates(rule.predicate):
        for field in _OBJECT_FIELDS:
            ref = getattr(pred, field, None)
            if isinstance(ref, str) and concept_root(ref) not in rule.concepts:
                yield "undefined_concept", f"{pred.op}.{field} names undeclared concept {ref!r}"
        zone = getattr(pred, "zone", None)
        if isinstance(zone, str) and zone not in rule.zones:
            yield "undefined_zone", f"{pred.op}.zone names undeclared zone {zone!r}"
    for name, spec in rule.zones.items():
        if spec.kind == "buffer" and (spec.buffer_of or "") not in rule.concepts:
            problem = f"zone {name!r} buffers undeclared concept {spec.buffer_of!r}"
            yield "undefined_concept", problem


def _out_of_range(value: float, low: float, high: float) -> bool:
    return not low <= value <= high


def _threshold_problems(rule: RuleIR) -> Iterator[str]:
    distance = f"outside {MIN_DISTANCE_M}-{MAX_DISTANCE_M} m"
    duration = f"outside {MIN_DURATION_S:g}-{MAX_DURATION_S:g} s"
    for pred in walk_predicates(rule.predicate):
        if isinstance(pred, DistanceM):
            for name, value in (("lt", pred.lt), ("gt", pred.gt)):
                if value is not None and _out_of_range(value, MIN_DISTANCE_M, MAX_DISTANCE_M):
                    yield f"distance_m.{name} = {value} m is {distance}"
        for name, seconds in _durations(pred):
            if _out_of_range(seconds, MIN_DURATION_S, MAX_DURATION_S):
                yield f"{pred.op}.{name} = {seconds} s is {duration}"
    for name, spec in rule.zones.items():
        if spec.buffer_m is not None and _out_of_range(
            spec.buffer_m, MIN_DISTANCE_M, MAX_DISTANCE_M
        ):
            yield f"zone {name!r} buffer_m = {spec.buffer_m} m is {distance}"


def _durations(pred: Predicate) -> list[tuple[str, float]]:
    if isinstance(pred, Persist | Absence):
        return [("seconds", pred.seconds)]
    if isinstance(pred, Dwell):
        return [("total_s_gte", pred.total_s_gte)]
    if isinstance(pred, RuleSequence):
        return [("require_within_s", pred.require_within_s)]
    return []


def _self_contradictions(rule: RuleIR) -> Iterator[str]:
    literals = _literals(rule.predicate)
    for subject, zone, present in sorted(literals):
        if present and (subject, zone, False) in literals:
            yield f"the condition needs {subject!r} both in and not in zone {zone!r}"


def _privacy_problems(rule: RuleIR, mode: PrivacyMode) -> Iterator[tuple[LintCode, str]]:
    if mode in PRIVATE_MODES:
        for name in face_concepts(rule):
            problem = f"concept {name!r} needs faces, but the site is in {mode} mode"
            yield "privacy_inconsistency", problem
    for name in private_space_zones(rule):
        yield "private_space_zone", f"zone {name!r} names a private space"


def _action_problems(rule: RuleIR) -> Iterator[tuple[LintCode, str]]:
    if not rule.actions:
        yield "missing_action", "the rule has no actions"
    if not isinstance(getattr(rule, "severity", None), Severity):  # e.g. built by model_construct
        yield "missing_severity", "the rule has no valid severity"


# --- helpers ------------------------------------------------------------------------------------

Literal_ = tuple[str, str, bool]  # (subject concept, zone, present?)


def _literals(pred: Predicate) -> set[Literal_]:
    """Zone-presence conditions the predicate requires: its own, or each conjunct's of an `all`."""
    conjuncts = pred.of if isinstance(pred, All) else (pred,)
    return {lit for c in conjuncts if (lit := _literal(c)) is not None}


def _literal(pred: Predicate) -> Literal_ | None:
    if isinstance(pred, Persist):
        return _literal(pred.of)
    if isinstance(pred, Inside | Dwell):
        return concept_root(pred.object), pred.zone, True
    if isinstance(pred, Count) and pred.zone is not None and pred.gt is not None:
        return concept_root(pred.object), pred.zone, True
    if isinstance(pred, Absence):
        return concept_root(pred.object), pred.zone, False
    if isinstance(pred, Not_) and isinstance(pred.of, Inside):
        return concept_root(pred.of.object), pred.of.zone, False
    return None


def _words(text: str) -> str:
    return re.sub(r"[_\-.]+", " ", text.lower())


def _names_private_space(name: str) -> bool:
    if prototype_mode():
        return False
    declined = deterministic_check(_words(name))
    return declined is not None and declined.category is GuardrailCategory.PRIVATE_SPACE
