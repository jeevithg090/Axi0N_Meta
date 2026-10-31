"""Acceptance and approval use the rule's camera scope (P2-06c). No network, no model.

Live finding (main f81f43e): "keep people 2 m from a moving forklift" on a two-camera site
(kitchen phone-9, warehouse phone-10). The planner found the rule observable on phone-10 only
and calibrated phone-10 from its pallets; the runtime runs the rule on phone-10 only (#73). But
`calibration_err<=tol` still asked for a calibration on every site camera ("camera 'phone-9'
has no ground calibration"), so the approval gate marked the rule amber.
"""

from __future__ import annotations

from collections.abc import Sequence

from rulecam.contracts import AcceptanceCheck, Calibration, FeasibilityVerdict, Probe, RuleIR
from rulecam.planner.acceptance import (
    CitationResult,
    PlannedRule,
    RepairLoop,
    SiteFacts,
    check_calibration,
    check_detect_lb,
)
from rulecam.planner.geometry import SiteGeometry
from rulecam.planner.models import Concern
from rulecam.planner.scope import rule_scope, scoped_cameras
from rulecam.runtime.activate import active_plan_from, gate_rule
from tests.rules.helpers import make_rule
from tests.runtime.activation_helpers import zoned_plan

KITCHEN, WAREHOUSE = "phone-9", "phone-10"
VIEWS: dict[str, tuple[int, int] | None] = {KITCHEN: (640, 480), WAREHOUSE: (640, 480)}
CONCERN = Concern(text="keep people 2 m from a moving forklift", source_ref="text")
PALLET_CAL = Calibration(
    camera_id=WAREHOUSE,
    homography=((0.01, 0.0, 0.0), (0.0, 0.01, 0.0), (0.0, 0.0, 1.0)),
    method="reference_object",
    reference="pallet_48x40in",
    err_m=0.04,
)


def _rule(calibration: str | None = f"ground-plane@{WAREHOUSE}") -> RuleIR:
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0},
        concepts={"person": "person", "forklift": "forklift"},
        rule_id="forklift-proximity",
        severity="high",
    )
    source = rule.source.model_copy(update={"text": CONCERN.text})
    return rule.model_copy(update={"source": source, "calibration": calibration})


def _probe(concept: str, camera_id: str, lb: float = 0.95) -> Probe:
    return Probe(
        concept=concept,
        camera_id=camera_id,
        n_frames=12,
        detect_rate=lb,
        detect_lb=lb,
        median_px_height=80.0,
    )


def _verdict(*cameras: str) -> FeasibilityVerdict:
    return FeasibilityVerdict(
        concern=CONCERN.text,
        verdict="observable",
        says="I can see the person, forklift",
        evidence=tuple(_probe(c, cam) for cam in cameras for c in ("person", "forklift")),
    )


def _loop() -> RepairLoop:
    """Acceptance on the live site: both cameras see people and forklifts; phone-10 calibrated."""
    facts = SiteFacts(
        views=VIEWS,
        probes=tuple(_probe(c, cam) for cam in VIEWS for c in ("person", "forklift")),
        calibrations={WAREHOUSE: PALLET_CAL},
    )
    return RepairLoop(
        facts,
        recompile=_no_recompile,
        probe=lambda _: [],
        verify_citation=lambda _: CitationResult(matched=True, detail="ok"),
    )


def _no_recompile(concern: Concern, feedback: Sequence[str]) -> RuleIR:
    raise AssertionError(f"nothing here is repairable: {feedback}")


def _approve(rule: RuleIR, verdict: FeasibilityVerdict) -> tuple[str, tuple[str, ...]]:
    """Acceptance, gate and activation for one rule: (gate status, the runtime's cameras)."""
    outcome = _loop().run([PlannedRule(concern=CONCERN, rule=rule)], [verdict])
    ci = [AcceptanceCheck(item=rule.id, check=c, passed=True) for c in ("ci_green", "nonvacuous")]
    site = zoned_plan(outcome.rules, [*outcome.acceptance.checks, *ci]).model_copy(
        update={"feasibility": outcome.feasibility}
    )
    (kept,) = site.rules
    gate = gate_rule(site, kept)
    active = active_plan_from(site, calibrations={WAREHOUSE: PALLET_CAL}).active_plan
    assert active.rule_cameras.get(kept.id, ()) == rule_scope(site.feasibility, kept)
    return gate.status, active.rule_cameras.get(kept.id, ())


def _calibration_check(outcome_checks: Sequence[AcceptanceCheck]) -> AcceptanceCheck:
    (check,) = [c for c in outcome_checks if c.check == "calibration_err<=tol"]
    return check


def test_the_live_case_passes_acceptance_and_gates_pass() -> None:
    rule, verdict = _rule(), _verdict(WAREHOUSE)
    outcome = _loop().run([PlannedRule(concern=CONCERN, rule=rule)], [verdict])
    assert outcome.acceptance.all_passed
    assert _calibration_check(outcome.acceptance.checks).passed
    assert outcome.questions == ()  # nobody is asked to calibrate the kitchen
    assert outcome.feasibility[0].verdict == "observable"
    assert _approve(rule, verdict) == ("pass", (WAREHOUSE,))


def test_a_rule_observable_on_both_cameras_with_one_calibrated_stays_amber() -> None:
    rule, verdict = _rule(calibration=None), _verdict(KITCHEN, WAREHOUSE)
    check = check_calibration(rule, {WAREHOUSE: PALLET_CAL}, VIEWS, (verdict,))
    assert not check.passed
    assert "'phone-9' has no ground calibration" in check.detail
    status, cameras = _approve(rule, verdict)
    assert status == "amber"
    assert cameras == (WAREHOUSE, KITCHEN)


def test_a_rule_without_camera_evidence_is_checked_on_every_camera() -> None:
    rule = _rule(calibration=None)
    assert scoped_cameras(rule, VIEWS) == [WAREHOUSE, KITCHEN]
    check = check_calibration(rule, {WAREHOUSE: PALLET_CAL}, VIEWS)
    assert not check.passed
    assert "'phone-9'" in check.detail


def test_detect_lb_is_measured_on_the_scope_only() -> None:
    rule, verdict = _rule(calibration=None), _verdict(WAREHOUSE)
    weak_on_warehouse = (
        _probe("person", KITCHEN),
        _probe("forklift", KITCHEN),
        _probe("person", WAREHOUSE, 0.5),
        _probe("forklift", WAREHOUSE),
    )
    assert check_detect_lb(rule, weak_on_warehouse, VIEWS).passed  # best over every camera
    scoped = check_detect_lb(rule, weak_on_warehouse, VIEWS, (verdict,))
    assert not scoped.passed
    assert "'phone-10'" in scoped.detail


def test_compile_names_the_calibration_of_the_scope_only() -> None:
    rule = _rule(calibration=None)
    unscoped = SiteGeometry(cameras=(KITCHEN, WAREHOUSE), calibrations={WAREHOUSE: PALLET_CAL})
    assert unscoped.calibration_name(rule) is None
    scoped = unscoped.model_copy(update={"feasibility": (_verdict(WAREHOUSE),)})
    assert scoped.calibration_name(rule) == f"ground-plane@{WAREHOUSE}"
    assert scoped.ground(rule).calibration == f"ground-plane@{WAREHOUSE}"
    both = unscoped.model_copy(update={"feasibility": (_verdict(KITCHEN, WAREHOUSE),)})
    assert both.calibration_name(rule) is None
