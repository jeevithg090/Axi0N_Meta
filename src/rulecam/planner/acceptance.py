"""Acceptance checks and the bounded repair loop: step 8 of the Site Planner (task P2-06c).

The fixed vocabulary is `CHECKS`. This step measures seven of them for every concern the gauge
found checkable (`observable` or `caveat`):

- `concern_covered`: the concern has a compiled rule without structural lint errors
  (undefined concepts or zones, unreasonable thresholds, contradictions, missing actions or
  severity);
- `zone_in_view`: every zone the rule uses is on one of the site's cameras, and a polygon zone
  lies wholly inside that camera's latest frame;
- `detect_lb>=0.8`: every concept of the rule has a SAM probe on the rule's camera with a Wilson
  lower bound of at least 0.8 (concepts not probed yet are probed here);
- `calibration_err<=tol`: a metric rule (distances, speeds, metric buffers) has a ground
  calibration on each of its cameras with `err_m <= CALIBRATION_TOL_M`;
- `privacy_consistent`: no concept needs faces in Anonymous or Strict Mode;
- `no_private_spaces`: no zone names a private space;
- `citation_text_match`: a rule that cites a standard has a citation whose quote is on the cited
  page (`planner.standards`).

`ci_green` and `nonvacuous` need Rule CI (P2-08) and are added by the prove step; nothing here
records them, so the trace never claims a check that did not run.

The trace is a strict AND. Failing items are repaired for at most `MAX_REPAIR_ROUNDS` rounds,
and only the failing items are re-compiled, with the failures as feedback. A repair that lowers
the rule's severity or drops one of its actions is rejected. What still fails afterwards is
handled honestly: the trace keeps the failures; a rule failing `concern_covered`, `zone_in_view`,
`privacy_consistent` or `no_private_spaces` is taken out of the plan and becomes a question for
the owner plus an amber (`caveat`) verdict; a rule failing only `detect_lb>=0.8`,
`calibration_err<=tol` or `citation_text_match` (camera, calibration or source fixes, which a
re-compile cannot make) stays with an amber verdict saying why. A metric rule whose camera has
no usable calibration also gets one question per such camera, asking the owner to mark a known
distance on its floor (a scale is never guessed).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Final

from pydantic import Field

from rulecam.contracts.core import Calibration, Frozen, PrivacyMode, Severity, Zone
from rulecam.contracts.plan import AcceptanceCheck, AcceptanceResult, FeasibilityVerdict, Probe
from rulecam.contracts.rule_ir import DistanceM, Moving, RuleIR, ZoneSpec
from rulecam.log import get_logger
from rulecam.perception.calibration import zone_in_view
from rulecam.perception.errors import ZoneError
from rulecam.planner.errors import GuardrailDeclinedError, RuleCompilationError
from rulecam.planner.lint import (
    PRIVATE_MODES,
    CameraViews,
    LintCode,
    LintFinding,
    cross_rule_contradictions,
    face_concepts,
    lint_rule,
    private_space_zones,
)
from rulecam.planner.models import Concern
from rulecam.planner.steps.ask import Question
from rulecam.planner.steps.compile import walk_predicates
from rulecam.planner.steps.survey import clean_concepts

_log = get_logger(__name__)

CHECKS: Final = (
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
RULE_CI_CHECKS: Final = frozenset({"ci_green", "nonvacuous"})
REPAIRABLE: Final = frozenset(
    {"concern_covered", "zone_in_view", "privacy_consistent", "no_private_spaces"}
)
MAX_REPAIR_ROUNDS: Final = 2
MIN_DETECT_LB: Final = 0.8
CALIBRATION_TOL_M: Final = 0.10
# Lint codes the acceptance checks measure themselves; the rest make a rule unusable.
_MEASURED_LINT: Final[frozenset[LintCode]] = frozenset(
    {"zone_out_of_view", "privacy_inconsistency", "private_space_zone"}
)
_SEVERITY_RANK: Final = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}


class CitationResult(Frozen):
    """Whether a concern's cited standard was verified on its source page."""

    matched: bool
    detail: str


class PlannedRule(Frozen):
    """A checkable concern and the rule compiled for it (`None` when compiling failed).

    Attributes:
        concern: The concern.
        rule: Its rule, if one compiled.
        error: Why there is no rule (the compile error).
    """

    concern: Concern
    rule: RuleIR | None = None
    error: str = ""


class SiteFacts(Frozen):
    """What the checks measure against.

    Attributes:
        views: The site's cameras and frame sizes.
        probes: Detectability probes, from step 3 and from this step.
        calibrations: Ground calibration per camera.
        privacy_mode: The site's privacy mode.
    """

    views: dict[str, tuple[int, int] | None]
    probes: tuple[Probe, ...] = ()
    calibrations: dict[str, Calibration] = Field(default_factory=dict)
    privacy_mode: PrivacyMode = PrivacyMode.STANDARD


class AcceptanceOutcome(Frozen):
    """The repair loop's result.

    Attributes:
        rules: The rules that stay in the plan.
        feasibility: The verdicts, with amber verdicts for what still fails.
        acceptance: The last round's trace (failures kept).
        questions: Questions for the owner about rules taken out.
    """

    rules: tuple[RuleIR, ...]
    feasibility: tuple[FeasibilityVerdict, ...]
    acceptance: AcceptanceResult
    questions: tuple[Question, ...]


Recompiler = Callable[[Concern, Sequence[str]], RuleIR]
"""Re-compiles a concern with feedback; raises `RuleCompilationError` or
`GuardrailDeclinedError`."""
Prober = Callable[[Sequence[str]], Sequence[Probe]]
"""Probes concept phrases on every camera."""
CitationVerifier = Callable[[Concern], CitationResult]
"""Grounds a concern's standard and checks the quote on the cited page."""


# --- the checks ---------------------------------------------------------------------------------


def _check(item: str, check: str, problems: Sequence[str], ok: str) -> AcceptanceCheck:
    passed = not problems
    return AcceptanceCheck(
        item=item, check=check, passed=passed, detail=ok if passed else "; ".join(problems)
    )


def rule_cameras(rule: RuleIR, views: CameraViews) -> list[str]:
    """The cameras a rule runs on: its zones' cameras, or every site camera if it has no zones."""
    cameras = {spec.camera_id for spec in rule.zones.values()}
    return sorted(cameras or views)


def check_zone_in_view(rule: RuleIR, views: CameraViews) -> AcceptanceCheck:
    """`zone_in_view`: every used zone's camera exists and a polygon zone is wholly in its frame.

    Args:
        rule: The rule.
        views: The site's cameras and frame sizes.

    Returns:
        The check.
    """
    used = {
        zone
        for p in walk_predicates(rule.predicate)
        if isinstance(zone := getattr(p, "zone", None), str)
    }
    problems = [
        problem
        for name in sorted(used)
        if (problem := _zone_problem(name, rule.zones[name], views)) is not None
    ]
    ok = f"{len(used)} zone(s) fully in view" if used else "the rule uses no zones"
    return _check(rule.id, "zone_in_view", problems, ok)


def _zone_problem(name: str, spec: ZoneSpec, views: CameraViews) -> str | None:
    if spec.camera_id not in views:
        return f"zone {name!r} is on camera {spec.camera_id!r}, which the site does not have"
    if spec.kind != "polygon":
        return None  # a buffer follows its concept; detect_lb>=0.8 checks the concept
    size = views[spec.camera_id]
    if size is None:
        return f"no readable frame from {spec.camera_id!r} to place zone {name!r}"
    zone = Zone(zone_id=name, camera_id=spec.camera_id, kind="polygon", polygon_px=spec.polygon_px)
    try:
        view = zone_in_view(zone, *size)
    except ZoneError as exc:
        return f"zone {name!r}: {exc}"
    if view.state != "inside":
        return f"only {view.visible_fraction:.0%} of zone {name!r} is in view of {spec.camera_id!r}"
    return None


def check_detect_lb(rule: RuleIR, probes: Sequence[Probe], views: CameraViews) -> AcceptanceCheck:
    """`detect_lb>=0.8`: each concept's best probe on the rule's cameras has `detect_lb >= 0.8`.

    Args:
        rule: The rule.
        probes: Available probes.
        views: The site's cameras (used when the rule has no zones).

    Returns:
        The check; a concept without a probe fails.
    """
    cameras = set(rule_cameras(rule, views))
    problems: list[str] = []
    lowest: tuple[float, str] | None = None
    for name, spec in sorted(rule.concepts.items()):
        phrase = _phrase(spec.prompt)
        found = [p for p in probes if p.concept == phrase and p.camera_id in cameras]
        if not found:
            problems.append(f"concept {name!r} ({phrase!r}) was not probed on {sorted(cameras)}")
            continue
        best = max(found, key=lambda p: p.detect_lb)
        if best.detect_lb < MIN_DETECT_LB:
            problems.append(
                f"concept {name!r} detect_lb={best.detect_lb:.2f} < {MIN_DETECT_LB} "
                f"on {best.camera_id!r}"
            )
        if lowest is None or best.detect_lb < lowest[0]:
            lowest = (best.detect_lb, name)
    ok = f"lowest detect_lb {lowest[0]:.2f} ({lowest[1]})" if lowest else "the rule has no concepts"
    return _check(rule.id, "detect_lb>=0.8", problems, ok)


def is_metric(rule: RuleIR) -> bool:
    """Whether a rule measures ground distance or speed (so it needs a calibration)."""
    if any(isinstance(p, DistanceM | Moving) for p in walk_predicates(rule.predicate)):
        return True
    return any(spec.buffer_m is not None for spec in rule.zones.values())


def check_calibration(
    rule: RuleIR, calibrations: Mapping[str, Calibration], views: CameraViews
) -> AcceptanceCheck:
    """`calibration_err<=tol`: a metric rule's cameras are calibrated within `CALIBRATION_TOL_M`.

    Args:
        rule: The rule.
        calibrations: Ground calibration per camera.
        views: The site's cameras (used when the rule has no zones).

    Returns:
        The check; a non-metric rule passes.
    """
    if not is_metric(rule):
        return _check(rule.id, "calibration_err<=tol", (), "the rule measures no distance")
    problems: list[str] = []
    for camera_id in rule_cameras(rule, views):
        calibration = calibrations.get(camera_id)
        if calibration is None:
            problems.append(
                f"camera {camera_id!r} has no ground calibration, so metres cannot be measured"
            )
        elif calibration.err_m > CALIBRATION_TOL_M:
            problems.append(
                f"camera {camera_id!r} calibration error {calibration.err_m:.2f} m > "
                f"{CALIBRATION_TOL_M:.2f} m"
            )
    ok = f"calibrated within {CALIBRATION_TOL_M:.2f} m"
    return _check(rule.id, "calibration_err<=tol", problems, ok)


def uncalibrated_cameras(
    rule: RuleIR, calibrations: Mapping[str, Calibration], views: CameraViews
) -> list[str]:
    """The cameras a metric rule runs on whose calibration is missing or outside tolerance."""
    if not is_metric(rule):
        return []
    return [
        camera_id
        for camera_id in rule_cameras(rule, views)
        if (cal := calibrations.get(camera_id)) is None or cal.err_m > CALIBRATION_TOL_M
    ]


def calibration_question(number: int, camera_id: str, concerns: Sequence[str]) -> Question:
    """Ask the owner to mark a known distance on a camera's floor, for its metric rules.

    Args:
        number: The question's number.
        camera_id: The camera without a usable calibration.
        concerns: The concerns that need metres there.

    Returns:
        A frame-tap question on that camera.
    """
    listed = "; ".join(repr(c) for c in concerns)
    return Question(
        question_id=f"calibrate-{number}",
        text=(
            f"To measure metres on {camera_id} for {listed}, I need a known distance on its "
            "floor. Tap four floor points whose positions you know (for example the corners of "
            "a 48x40 in pallet or a 1 m square of tape), or add them under "
            f"cameras.{camera_id}.calibration in the site config."
        ),
        target_field="calibration",
        requires_frame_tap=True,
        camera_id=camera_id,
    )


def check_privacy_consistent(rule: RuleIR, mode: PrivacyMode) -> AcceptanceCheck:
    """`privacy_consistent`: no concept needs faces in Anonymous or Strict Mode."""
    faces = face_concepts(rule) if mode in PRIVATE_MODES else []
    problems = [f"concept {name!r} needs faces, but the site is in {mode} mode" for name in faces]
    return _check(rule.id, "privacy_consistent", problems, f"consistent with {mode} mode")


def check_no_private_spaces(rule: RuleIR) -> AcceptanceCheck:
    """`no_private_spaces`: no zone of the rule names a private space."""
    problems = [f"zone {name!r} names a private space" for name in private_space_zones(rule)]
    return _check(rule.id, "no_private_spaces", problems, "no private-space zones")


def check_citation(rule: RuleIR, citation: CitationResult | None) -> AcceptanceCheck:
    """`citation_text_match`: a rule citing a standard has a verified quote.

    Args:
        rule: The rule.
        citation: The concern's citation check, `None` when the rule cites nothing.

    Returns:
        The check.
    """
    if not rule.source.standard:
        return _check(rule.id, "citation_text_match", (), "the rule cites no standard")
    if citation is None:
        return _check(rule.id, "citation_text_match", ["the cited standard was not checked"], "")
    problems = [] if citation.matched else [citation.detail]
    return _check(rule.id, "citation_text_match", problems, citation.detail)


def check_concern_covered(planned: PlannedRule, lint: Sequence[LintFinding]) -> AcceptanceCheck:
    """`concern_covered`: the concern has a usable rule (compiled, no structural lint errors)."""
    if planned.rule is None:
        problems = [f"no rule compiled: {planned.error or 'unknown error'}"]
        return _check(planned.concern.text, "concern_covered", problems, "")
    problems = [f"[{f.code}] {f.message}" for f in lint]
    return _check(planned.concern.text, "concern_covered", problems, f"rule {planned.rule.id!r}")


def check_rule(
    rule: RuleIR, facts: SiteFacts, citation: CitationResult | None
) -> list[AcceptanceCheck]:
    """The six per-rule checks, in vocabulary order."""
    return [
        check_zone_in_view(rule, facts.views),
        check_detect_lb(rule, facts.probes, facts.views),
        check_calibration(rule, facts.calibrations, facts.views),
        check_privacy_consistent(rule, facts.privacy_mode),
        check_citation(rule, citation),
        check_no_private_spaces(rule),
    ]


# --- the loop -----------------------------------------------------------------------------------


class _Round(Frozen):
    """One evaluation: the checks per planned rule (same order)."""

    checks: tuple[tuple[AcceptanceCheck, ...], ...]

    def failing(self, index: int) -> list[AcceptanceCheck]:
        return [c for c in self.checks[index] if not c.passed]

    def flat(self) -> tuple[AcceptanceCheck, ...]:
        return tuple(c for group in self.checks for c in group)


class RepairLoop:
    """Runs the checks and repairs failing rules for at most `MAX_REPAIR_ROUNDS` rounds."""

    def __init__(
        self,
        facts: SiteFacts,
        *,
        recompile: Recompiler,
        probe: Prober,
        verify_citation: CitationVerifier,
    ) -> None:
        """Create the loop.

        Args:
            facts: What the checks measure against.
            recompile: Re-compiles one concern with feedback.
            probe: Probes concepts that have no probe yet.
            verify_citation: Checks a concern's cited standard.
        """
        self._facts = facts
        self._recompile = recompile
        self._probe = probe
        self._verify_citation = verify_citation
        self._citations: dict[str, CitationResult] = {}

    def run(
        self, planned: Sequence[PlannedRule], feasibility: Sequence[FeasibilityVerdict]
    ) -> AcceptanceOutcome:
        """Check, repair the failing items, and settle what still fails.

        Args:
            planned: One entry per checkable concern.
            feasibility: The verdicts so far (one per concern).

        Returns:
            The outcome.
        """
        items = list(planned)
        verdicts = {v.concern: v for v in feasibility}
        current = self._evaluate(items)
        rounds = 0
        while rounds < MAX_REPAIR_ROUNDS and self._repairable(current):
            rounds += 1
            for index in self._repairable(current):
                items[index] = self._repair(items[index], current.failing(index), verdicts)
            items = [p for p in items if verdicts[p.concern.text].verdict != "declined"]
            current = self._evaluate(items)
        return self._settle(items, current, verdicts, rounds)

    def _repairable(self, current: _Round) -> list[int]:
        return [
            i
            for i in range(len(current.checks))
            if any(c.check in REPAIRABLE for c in current.failing(i))
        ]

    def _evaluate(self, items: Sequence[PlannedRule]) -> _Round:
        self._probe_missing([p.rule for p in items if p.rule is not None])
        rules = [p.rule for p in items if p.rule is not None]
        contradictions = cross_rule_contradictions(rules)
        groups: list[tuple[AcceptanceCheck, ...]] = []
        for planned in items:
            rule = planned.rule
            lint = [] if rule is None else self._structural_lint(rule, contradictions)
            covered = check_concern_covered(planned, lint)
            if rule is None or lint:
                groups.append((covered,))
                continue
            citation = self._citation(planned) if rule.source.standard else None
            groups.append((covered, *check_rule(rule, self._facts, citation)))
        return _Round(checks=tuple(groups))

    def _structural_lint(
        self, rule: RuleIR, contradictions: Iterable[LintFinding]
    ) -> list[LintFinding]:
        own = lint_rule(rule, views=self._facts.views, privacy_mode=self._facts.privacy_mode)
        return [
            f
            for f in (*own, *(c for c in contradictions if c.rule_id == rule.id))
            if f.code not in _MEASURED_LINT
        ]

    def _probe_missing(self, rules: Sequence[RuleIR]) -> None:
        probed = {p.concept for p in self._facts.probes}
        wanted = [
            phrase
            for rule in rules
            for spec in rule.concepts.values()
            if (phrase := _phrase(spec.prompt)) not in probed
        ]
        if wanted:
            fresh = tuple(self._probe(list(dict.fromkeys(wanted))))
            self._facts = self._facts.model_copy(update={"probes": (*self._facts.probes, *fresh)})

    def _citation(self, planned: PlannedRule) -> CitationResult:
        key = planned.concern.text
        if key not in self._citations:
            self._citations[key] = self._verify_citation(planned.concern)
        return self._citations[key]

    def _repair(
        self,
        planned: PlannedRule,
        failing: Sequence[AcceptanceCheck],
        verdicts: dict[str, FeasibilityVerdict],
    ) -> PlannedRule:
        feedback = [f"{c.check}: {c.detail}" for c in failing]
        concern = planned.concern
        try:
            new = self._recompile(concern, feedback)
        except GuardrailDeclinedError as declined:
            verdicts[concern.text] = declined.declined.to_feasibility_verdict(concern.text)
            return planned
        except RuleCompilationError as exc:
            _log.warning("acceptance_repair_failed", concern=concern.text, error=str(exc))
            return (
                planned
                if planned.rule is not None
                else planned.model_copy(update={"error": str(exc)})
            )
        loosened = loosening(planned.rule, new)
        if loosened is not None:
            _log.warning("acceptance_repair_rejected", rule_id=new.id, reason=loosened)
            return planned
        _log.info("acceptance_repaired", concern=concern.text, rule_id=new.id)
        return PlannedRule(concern=concern, rule=new)

    def _settle(
        self,
        items: Sequence[PlannedRule],
        last: _Round,
        verdicts: dict[str, FeasibilityVerdict],
        rounds: int,
    ) -> AcceptanceOutcome:
        rules: list[RuleIR] = []
        questions: list[Question] = []
        for index, planned in enumerate(items):
            failing = last.failing(index)
            blocking = [c for c in failing if c.check in REPAIRABLE]
            if not blocking and planned.rule is not None:
                rules.append(planned.rule)
            if failing:
                verdicts[planned.concern.text] = _amber(
                    verdicts[planned.concern.text], failing, blocking
                )
            if blocking:
                questions.append(_question(len(questions) + 1, planned, blocking))
        questions.extend(self._calibration_questions(rules, items))
        return AcceptanceOutcome(
            rules=tuple(rules),
            feasibility=tuple(verdicts.values()),
            acceptance=AcceptanceResult(checks=last.flat(), rounds=rounds),
            questions=tuple(questions),
        )

    def _calibration_questions(
        self, rules: Sequence[RuleIR], items: Sequence[PlannedRule]
    ) -> list[Question]:
        concern_of = {p.rule.id: p.concern.text for p in items if p.rule is not None}
        needing: dict[str, list[str]] = {}
        for rule in rules:
            for camera_id in uncalibrated_cameras(
                rule, self._facts.calibrations, self._facts.views
            ):
                needing.setdefault(camera_id, []).append(concern_of[rule.id])
        return [
            calibration_question(number, camera_id, concerns)
            for number, (camera_id, concerns) in enumerate(sorted(needing.items()), start=1)
        ]


def loosening(old: RuleIR | None, new: RuleIR) -> str | None:
    """Why a repaired rule is weaker than the one it replaces, or `None`.

    A repair may not lower the severity or drop an action kind.

    Args:
        old: The rule before repair (`None` when there was none).
        new: The repaired rule.

    Returns:
        The reason the repair is rejected, or `None`.
    """
    if old is None:
        return None
    if _SEVERITY_RANK[new.severity] < _SEVERITY_RANK[old.severity]:
        return f"severity lowered from {old.severity} to {new.severity}"
    dropped = {a.kind for a in old.actions} - {a.kind for a in new.actions}
    if dropped:
        return f"actions dropped: {', '.join(sorted(dropped))}"
    return None


def _amber(
    verdict: FeasibilityVerdict,
    failing: Sequence[AcceptanceCheck],
    blocking: Sequence[AcceptanceCheck],
) -> FeasibilityVerdict:
    reasons = "; ".join(c.detail for c in failing)
    if blocking:
        says = f"I can't check this yet; I need your answer first ({reasons})."
    else:
        says = f"I can check this, with a caveat: {reasons}."
    return verdict.model_copy(update={"verdict": "caveat", "says": says})


def _question(number: int, planned: PlannedRule, blocking: Sequence[AcceptanceCheck]) -> Question:
    reasons = "; ".join(c.detail for c in blocking)
    return Question(
        question_id=f"accept-{number}",
        text=(
            f"I couldn't build a rule I can check for {planned.concern.text!r} ({reasons}). "
            "What should change: the zone, the camera, or the request?"
        ),
        target_field="rules",
    )


def _phrase(prompt: str) -> str:
    cleaned = clean_concepts([prompt])
    return cleaned[0] if cleaned else prompt
