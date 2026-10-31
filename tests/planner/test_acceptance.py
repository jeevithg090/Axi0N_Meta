"""Acceptance checks and the bounded repair loop (task P2-06c). No network, no model."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from rulecam.contracts.core import Calibration, PrivacyMode, Severity
from rulecam.contracts.plan import FeasibilityVerdict, Probe
from rulecam.contracts.rule_ir import Absence, ActionSpec, DistanceM, Inside, RuleIR
from rulecam.planner.acceptance import (
    CHECKS,
    MAX_REPAIR_ROUNDS,
    CitationResult,
    PlannedRule,
    RepairLoop,
    SiteFacts,
    check_calibration,
    check_citation,
    check_detect_lb,
    check_no_private_spaces,
    check_privacy_consistent,
    check_zone_in_view,
    loosening,
)
from rulecam.planner.errors import GuardrailDeclinedError, RuleCompilationError
from rulecam.planner.guardrails import Declined, GuardrailCategory
from rulecam.planner.models import Concern

RULES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"
VIEWS: dict[str, tuple[int, int] | None] = {"cam1": (640, 480)}
CONCERN = Concern(text="wash hands before the line", source_ref="text")
VERIFIED = CitationResult(matched=True, detail="Food Code §2-301.12: quote found on the cited page")


def _rule(**update: object) -> RuleIR:
    raw = yaml.safe_load((RULES_DIR / "wash-before-line.rule.yaml").read_text(encoding="utf-8"))
    return RuleIR.model_validate({**raw, **update})


def _probe(concept: str, lb: float, camera_id: str = "cam1") -> Probe:
    return Probe(
        concept=concept,
        camera_id=camera_id,
        n_frames=24,
        detect_rate=min(1.0, lb + 0.1),
        detect_lb=lb,
        median_px_height=80.0,
    )


PROBES = tuple(_probe(c, 0.9) for c in ("person", "hands", "sink"))
CALIBRATED = {
    "cam1": Calibration(
        camera_id="cam1",
        homography=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        method="clicks4",
        err_m=0.05,
    )
}
FACTS = SiteFacts(
    views=VIEWS, probes=PROBES, calibrations=CALIBRATED, privacy_mode=PrivacyMode.ANONYMOUS
)
OBSERVABLE = FeasibilityVerdict(concern=CONCERN.text, verdict="observable", says="I can see it")


def test_the_vocabulary_is_the_task_books() -> None:
    assert CHECKS == (
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
    assert MAX_REPAIR_ROUNDS == 2


# --- single checks ------------------------------------------------------------------------------


def test_zone_in_view_measures_the_polygon_against_the_frame() -> None:
    assert check_zone_in_view(_rule(), VIEWS).passed
    partial = check_zone_in_view(_rule(), {"cam1": (200, 300)})
    assert not partial.passed
    assert "only 50% of zone 'line_entry'" in partial.detail
    unknown = check_zone_in_view(_rule(), {"cam1": None})
    assert not unknown.passed
    assert "no readable frame" in unknown.detail
    missing = check_zone_in_view(_rule(), {"cam2": (640, 480)})
    assert "which the site does not have" in missing.detail


def test_detect_lb_needs_a_probe_of_every_concept_on_the_rules_camera() -> None:
    assert check_detect_lb(_rule(), PROBES, VIEWS).detail == "lowest detect_lb 0.90 (hands)"
    low = check_detect_lb(_rule(), (*PROBES[:2], _probe("sink", 0.62)), VIEWS)
    assert not low.passed
    assert "concept 'sink' detect_lb=0.62 < 0.8 on 'cam1'" in low.detail
    elsewhere = check_detect_lb(_rule(), (*PROBES[:2], _probe("sink", 0.95, "cam2")), VIEWS)
    assert "concept 'sink' ('sink') was not probed" in elsewhere.detail


def test_calibration_is_required_only_for_metric_rules() -> None:
    assert check_calibration(_rule(), CALIBRATED, VIEWS).passed  # sink buffer is metric
    assert "has no ground calibration" in check_calibration(_rule(), {}, VIEWS).detail
    loose = {"cam1": CALIBRATED["cam1"].model_copy(update={"err_m": 0.3})}
    assert "0.30 m > 0.10 m" in check_calibration(_rule(), loose, VIEWS).detail
    plain = _rule(zones={"line_entry": _rule().zones["line_entry"].model_dump()})
    plain = plain.model_copy(update={"predicate": Inside(object="person", zone="line_entry")})
    assert check_calibration(plain, {}, VIEWS).detail == "the rule measures no distance"
    near = plain.model_copy(update={"predicate": DistanceM(a="person", b="sink", lt=1.0)})
    assert not check_calibration(near, {}, VIEWS).passed


def test_privacy_and_private_space_checks() -> None:
    concepts = {**_rule().model_dump()["concepts"], "mask": {"prompt": "face mask"}}
    faces = _rule(concepts=concepts)
    assert not check_privacy_consistent(faces, PrivacyMode.ANONYMOUS).passed
    assert check_privacy_consistent(faces, PrivacyMode.STANDARD).passed
    zones = {
        **_rule().model_dump()["zones"],
        "staff_restroom": _rule().model_dump()["zones"]["line_entry"],
    }
    check = check_no_private_spaces(_rule(zones=zones))
    assert (check.passed, check.detail) == (False, "zone 'staff_restroom' names a private space")


def test_citation_check_uses_the_measured_result() -> None:
    assert check_citation(_rule(), VERIFIED).passed
    failed = CitationResult(matched=False, detail="Food Code: quote not found on the cited page")
    assert check_citation(_rule(), failed).detail == failed.detail
    assert not check_citation(_rule(), None).passed
    uncited = _rule(source={"kind": "plan"})
    assert check_citation(uncited, None).detail == "the rule cites no standard"


def test_a_repair_may_not_loosen_the_rule() -> None:
    rule = _rule()
    assert loosening(None, rule) is None
    assert loosening(rule, rule) is None
    lower = rule.model_copy(update={"severity": Severity.LOW})
    assert loosening(rule, lower) == "severity lowered from medium to low"
    fewer = rule.model_copy(update={"actions": (ActionSpec(kind="log"),)})
    assert loosening(rule, fewer) == "actions dropped: coach_station, notify"


# --- the loop -----------------------------------------------------------------------------------


class Recompiler:
    """Fake re-compiler: answers from a list, recording the feedback it got."""

    def __init__(self, answers: Sequence[RuleIR | Exception]) -> None:
        self.answers = list(answers)
        self.feedback: list[list[str]] = []

    def __call__(self, concern: Concern, feedback: Sequence[str]) -> RuleIR:
        self.feedback.append(list(feedback))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _loop(
    recompile: Recompiler,
    *,
    facts: SiteFacts = FACTS,
    probed: list[list[str]] | None = None,
    citation: CitationResult = VERIFIED,
) -> RepairLoop:
    def probe(concepts: Sequence[str]) -> list[Probe]:
        if probed is not None:
            probed.append(list(concepts))
        return [_probe(c, 0.95) for c in concepts]

    return RepairLoop(facts, recompile=recompile, probe=probe, verify_citation=lambda _: citation)


def test_a_clean_rule_passes_without_repair() -> None:
    recompile = Recompiler([])
    outcome = _loop(recompile).run([PlannedRule(concern=CONCERN, rule=_rule())], [OBSERVABLE])
    assert outcome.acceptance.all_passed
    assert outcome.acceptance.rounds == 0
    assert len(outcome.acceptance.checks) == 7
    assert recompile.feedback == []
    assert outcome.feasibility == (OBSERVABLE,)


def test_unprobed_concepts_are_probed_before_checking() -> None:
    probed: list[list[str]] = []
    facts = FACTS.model_copy(update={"probes": PROBES[:1]})
    outcome = _loop(Recompiler([]), facts=facts, probed=probed).run(
        [PlannedRule(concern=CONCERN, rule=_rule())], [OBSERVABLE]
    )
    assert probed == [["hands", "sink"]]
    assert outcome.acceptance.all_passed


def test_only_the_failing_items_are_recompiled() -> None:
    other = Concern(text="keep the exit clear", source_ref="text")
    exit_rule = _rule(id="exit-clear", source={"kind": "plan"})
    zones = {
        **_rule().model_dump()["zones"],
        "locker_room": _rule().model_dump()["zones"]["line_entry"],
    }
    recompile = Recompiler([_rule()])
    outcome = _loop(recompile).run(
        [
            PlannedRule(concern=CONCERN, rule=_rule(zones=zones)),
            PlannedRule(concern=other, rule=exit_rule),
        ],
        [OBSERVABLE, OBSERVABLE.model_copy(update={"concern": other.text})],
    )
    assert recompile.feedback == [["no_private_spaces: zone 'locker_room' names a private space"]]
    assert outcome.acceptance.rounds == 1
    assert outcome.acceptance.all_passed


def test_a_structural_lint_error_fails_concern_covered_and_is_repaired() -> None:
    broken = _rule().model_copy(update={"predicate": Inside(object="ghost", zone="line_entry")})
    recompile = Recompiler([_rule()])
    outcome = _loop(recompile).run([PlannedRule(concern=CONCERN, rule=broken)], [OBSERVABLE])
    assert recompile.feedback == [
        ["concern_covered: [undefined_concept] inside.object names undeclared concept 'ghost'"]
    ]
    assert outcome.acceptance.all_passed


def test_a_loosening_repair_is_rejected_and_the_item_ends_as_a_question() -> None:
    zones = {
        **_rule().model_dump()["zones"],
        "break_room": _rule().model_dump()["zones"]["line_entry"],
    }
    weaker = _rule(severity="low")
    recompile = Recompiler([weaker, weaker])
    outcome = _loop(recompile).run(
        [PlannedRule(concern=CONCERN, rule=_rule(zones=zones))], [OBSERVABLE]
    )
    assert outcome.acceptance.rounds == 2
    assert outcome.rules == ()
    assert len(outcome.questions) == 1
    assert outcome.feasibility[0].verdict == "caveat"


def test_amber_failures_are_not_recompiled_and_keep_the_rule() -> None:
    facts = FACTS.model_copy(update={"probes": (*PROBES[:2], _probe("sink", 0.6))})
    recompile = Recompiler([])
    outcome = _loop(recompile, facts=facts).run(
        [PlannedRule(concern=CONCERN, rule=_rule())], [OBSERVABLE]
    )
    assert recompile.feedback == []
    assert outcome.acceptance.rounds == 0
    assert not outcome.acceptance.all_passed
    assert [r.id for r in outcome.rules] == ["wash-before-line"]
    assert outcome.questions == ()
    verdict = outcome.feasibility[0]
    assert verdict.verdict == "caveat"
    assert "detect_lb=0.60" in verdict.says


def test_a_compile_error_is_retried_twice_then_asked_about() -> None:
    error = RuleCompilationError("Rule compilation failed after repair round: bad node")
    recompile = Recompiler([error, error])
    outcome = _loop(recompile).run([PlannedRule(concern=CONCERN, error="bad node")], [OBSERVABLE])
    assert len(recompile.feedback) == MAX_REPAIR_ROUNDS
    assert recompile.feedback[0] == ["concern_covered: no rule compiled: bad node"]
    [check] = outcome.acceptance.checks
    assert check.check == "concern_covered"
    assert "Rule compilation failed" in check.detail
    assert outcome.rules == ()
    assert len(outcome.questions) == 1


def test_a_guardrail_decline_during_repair_declines_the_concern() -> None:
    declined = Declined(
        category=GuardrailCategory.PRIVATE_SPACE, reason="private space", alternative="the hall"
    )
    zones = {
        **_rule().model_dump()["zones"],
        "restroom": _rule().model_dump()["zones"]["line_entry"],
    }
    recompile = Recompiler([GuardrailDeclinedError(declined)])
    outcome = _loop(recompile).run(
        [PlannedRule(concern=CONCERN, rule=_rule(zones=zones))], [OBSERVABLE]
    )
    assert outcome.rules == ()
    assert outcome.acceptance.checks == ()
    assert outcome.feasibility[0].verdict == "declined"
    assert outcome.questions == ()


@pytest.mark.parametrize("mode", [PrivacyMode.ANONYMOUS, PrivacyMode.STRICT])
def test_cross_rule_contradictions_block_the_later_rule(mode: PrivacyMode) -> None:
    other = Concern(text="keep the line entry clear", source_ref="text")
    keep_out = _rule(id="keep-out").model_copy(
        update={"predicate": Inside(object="person", zone="line_entry")}
    )
    staffed = _rule(id="staffed").model_copy(
        update={"predicate": Absence(object="person", zone="line_entry", seconds=60)}
    )
    facts = FACTS.model_copy(update={"privacy_mode": mode})
    recompile = Recompiler([staffed, staffed])
    outcome = _loop(recompile, facts=facts).run(
        [PlannedRule(concern=CONCERN, rule=keep_out), PlannedRule(concern=other, rule=staffed)],
        [OBSERVABLE, OBSERVABLE.model_copy(update={"concern": other.text})],
    )
    assert [r.id for r in outcome.rules] == ["keep-out"]
    assert "contradictory_rules" in recompile.feedback[0][0]
