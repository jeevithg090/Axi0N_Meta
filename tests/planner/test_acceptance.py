"""Unit tests for executable acceptance checks and bounded repair loop (task P2-06c).

Verifies:
  - All 9 fixed vocabulary acceptance checks.
  - Strict AND aggregation of checks (AcceptanceResult.all_passed).
  - The repair loop fixes a seeded failure in 1 round with a fake LLM.
  - The repair loop gives up after 2 rounds and demotes the failing item to a Question
    and caveat verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from pydantic import BaseModel

from rulecam.contracts.core import Calibration, PrivacyMode
from rulecam.contracts.plan import (
    AcceptanceResult,
    FeasibilityVerdict,
    InventoryItem,
    Probe,
    RequestItem,
    SitePlan,
)
from rulecam.contracts.rule_ir import (
    DistanceM,
    Inside,
    RuleIR,
    ZoneSpec,
)
from rulecam.planner.acceptance import (
    FIXED_ACCEPTANCE_CHECKS,
    repair_plan_acceptance,
    run_acceptance_checks,
)
from rulecam.planner.models import Concern

M = TypeVar("M", bound=BaseModel)
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _make_valid_rule() -> RuleIR:
    raw = yaml.safe_load((FIXTURES_DIR / "wash-before-line.rule.yaml").read_text(encoding="utf-8"))
    return RuleIR.model_validate(raw)


def _make_test_plan(rule: RuleIR | None = None) -> SitePlan:
    active_rule = rule or _make_valid_rule()
    return SitePlan(
        plan_id="plan_acc_test",
        version=1,
        site_name="Acc Test Site",
        setting_inferred="commercial_kitchen",
        languages=("en",),
        request=(RequestItem(kind="text", text="Wash hands"),),
        inventory=(
            InventoryItem(concept="person", camera_id="cam1", count=2, mean_confidence=0.9),
            InventoryItem(concept="hands", camera_id="cam1", count=2, mean_confidence=0.9),
            InventoryItem(concept="sink", camera_id="cam1", count=1, mean_confidence=0.95),
        ),
        feasibility=(
            FeasibilityVerdict(
                concern="Wash hands",
                verdict="observable",
                says="Clear view",
            ),
        ),
        rules=(active_rule,),
        privacy_mode=PrivacyMode.STANDARD,
        retention_days=30,
        protected_two_key=(),
        roles={"shift_lead": "web_push"},
        acceptance=AcceptanceResult(checks=(), rounds=0),
        standards=(),
    )


class FakeRepairSpark:
    """Fake LLM client providing repaired rules on subsequent calls."""

    def __init__(self, responses: list[RuleIR]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
    ) -> M:
        self.calls.append({"model": model, "messages": messages})
        if self.responses:
            return self.responses.pop(0)  # type: ignore[return-value]
        raise RuntimeError("No remaining responses in FakeRepairSpark")


def test_acceptance_fixed_vocabulary_contains_all_9() -> None:
    """Verify that the 9 fixed vocabulary checks are defined."""
    assert len(FIXED_ACCEPTANCE_CHECKS) == 9
    expected = {
        "zone_in_view",
        "detect_lb>=0.8",
        "calibration_err<=tol",
        "ci_green",
        "nonvacuous",
        "privacy_consistent",
        "concern_covered",
        "citation_text_match",
        "no_private_spaces",
    }
    assert set(FIXED_ACCEPTANCE_CHECKS) == expected


def test_clean_plan_passes_all_acceptance_checks() -> None:
    """A valid plan with valid probes, calibrations and views passes all acceptance checks."""
    plan = _make_test_plan()
    probes = [
        Probe(
            concept="person",
            camera_id="cam1",
            n_frames=12,
            detect_rate=1.0,
            detect_lb=0.85,
            median_px_height=80.0,
        ),
        Probe(
            concept="hands",
            camera_id="cam1",
            n_frames=12,
            detect_rate=0.95,
            detect_lb=0.82,
            median_px_height=40.0,
        ),
        Probe(
            concept="sink",
            camera_id="cam1",
            n_frames=12,
            detect_rate=0.99,
            detect_lb=0.91,
            median_px_height=70.0,
        ),
    ]

    good_calib = {
        "cam1": Calibration(
            camera_id="cam1",
            homography=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            method="clicks4",
            err_m=0.08,
        )
    }

    res = run_acceptance_checks(
        plan,
        probes=probes,
        calibrations=good_calib,
        available_cameras=["cam1"],
        ci_status="green",
        concerns=[Concern(text="Wash hands", source_ref="text")],
    )

    assert res.all_passed is True
    assert len(res.checks) >= 9


def test_acceptance_detect_lb_check_fails_when_below_threshold() -> None:
    """Check detect_lb>=0.8 fails when a required concept has detect_lb < 0.8."""
    plan = _make_test_plan()
    probes = [
        Probe(
            concept="hands",
            camera_id="cam1",
            n_frames=12,
            detect_rate=0.6,
            detect_lb=0.45,  # Below 0.8
            median_px_height=20.0,
        ),
    ]
    res = run_acceptance_checks(plan, probes=probes, available_cameras=["cam1"])
    assert res.all_passed is False
    failed = [c for c in res.checks if not c.passed]
    assert any(c.check == "detect_lb>=0.8" for c in failed)


def test_acceptance_calibration_error_check() -> None:
    """Check calibration_err<=tol checks ground plane tolerance."""
    base = _make_valid_rule()
    metric_rule = base.model_copy(
        update={
            "predicate": DistanceM(a="person", b="sink", lt=1.5),
        }
    )
    plan = _make_test_plan(rule=metric_rule)

    # Missing calibration when required
    res_missing = run_acceptance_checks(plan, calibrations=None, available_cameras=["cam1"])
    assert any(c.check == "calibration_err<=tol" and not c.passed for c in res_missing.checks)

    # Calibration error exceeding tolerance (0.35m > 0.20m)
    bad_calib = {
        "cam1": Calibration(
            camera_id="cam1",
            homography=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            method="clicks4",
            err_m=0.35,
        )
    }
    res_err = run_acceptance_checks(plan, calibrations=bad_calib, available_cameras=["cam1"])
    assert any(c.check == "calibration_err<=tol" and not c.passed for c in res_err.checks)

    # Good calibration (0.08m <= 0.20m)
    good_calib = {
        "cam1": Calibration(
            camera_id="cam1",
            homography=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            method="clicks4",
            err_m=0.08,
        )
    }
    res_good = run_acceptance_checks(plan, calibrations=good_calib, available_cameras=["cam1"])
    assert any(c.check == "calibration_err<=tol" and c.passed for c in res_good.checks)


def test_acceptance_nonvacuous_check_fails_on_zero_threshold() -> None:
    """Vacuous rule with 0.0 distance or 0.0 dwell threshold fails nonvacuous check."""
    base = _make_valid_rule()
    vacuous_rule = base.model_copy(
        update={
            "predicate": DistanceM(a="person", b="sink", lt=0.0),
        }
    )
    plan = _make_test_plan(rule=vacuous_rule)
    res = run_acceptance_checks(plan, available_cameras=["cam1"])
    assert any(c.check == "nonvacuous" and not c.passed for c in res.checks)


def test_acceptance_no_private_spaces_fails_on_restroom_zone() -> None:
    """Rule with zone in private space fails no_private_spaces check."""
    base = _make_valid_rule()
    bad_zones = dict(base.zones)
    bad_zones["staff_restroom"] = ZoneSpec(
        kind="polygon",
        camera_id="cam1",
        polygon_px=((0, 0), (10, 0), (10, 10), (0, 10)),
    )
    bad_rule = base.model_copy(update={"zones": bad_zones})
    plan = _make_test_plan(rule=bad_rule)
    res = run_acceptance_checks(plan, available_cameras=["cam1"])
    assert any(c.check == "no_private_spaces" and not c.passed for c in res.checks)


def test_acceptance_repair_loop_fixes_in_1_round() -> None:
    """Repair loop detects failure, recompiles in 1 round, and passes."""
    base = _make_valid_rule()
    # Initially failing: nonvacuous (threshold 0)
    failing_rule = base.model_copy(
        update={
            "id": "R-FAILING",
            "predicate": DistanceM(a="person", b="sink", lt=0.0),
        }
    )
    plan = _make_test_plan(rule=failing_rule)

    # Repaired rule provided by fake spark
    fixed_rule = base.model_copy(
        update={
            "id": "R-FAILING",
            "predicate": DistanceM(a="person", b="sink", lt=1.5),
        }
    )
    spark = FakeRepairSpark([fixed_rule])

    good_calib = {
        "cam1": Calibration(
            camera_id="cam1",
            homography=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            method="clicks4",
            err_m=0.08,
        )
    }

    repaired_plan, acc, questions = repair_plan_acceptance(
        plan,
        spark=spark,
        calibrations=good_calib,
        available_cameras=["cam1"],
        max_rounds=2,
    )

    assert acc.all_passed is True
    assert acc.rounds == 1
    assert len(questions) == 0
    assert len(spark.calls) == 1
    assert repaired_plan.rules[0].predicate.op == "distance_m"


def test_acceptance_repair_loop_gives_up_after_2_rounds_and_creates_question() -> None:
    """Repair loop terminates after 2 rounds on persistent failure and creates Question."""
    base = _make_valid_rule()
    # Persistent failing rule (zone_out_of_view: camera cam99 not available)
    bad_zones = {
        "isolated_dock": ZoneSpec(
            kind="polygon",
            camera_id="cam99",
            polygon_px=((0, 0), (10, 0), (10, 10), (0, 10)),
        )
    }
    persistent_bad_rule = base.model_copy(
        update={
            "id": "R-PERSISTENT-FAIL",
            "zones": bad_zones,
            "predicate": Inside(object="person", zone="isolated_dock"),
        }
    )
    plan = _make_test_plan(rule=persistent_bad_rule)

    # Spark attempts to fix twice, but keeps failing zone_in_view
    spark = FakeRepairSpark([persistent_bad_rule, persistent_bad_rule])

    repaired_plan, acc, questions = repair_plan_acceptance(
        plan,
        spark=spark,
        available_cameras=["cam1"],  # cam99 not in available cameras
        max_rounds=2,
    )

    # Verify bounded at 2 rounds
    assert acc.rounds == 2
    # Failing rule was demoted, so question created
    assert len(questions) == 1
    assert questions[0].question_id == "q_repair_R-PERSISTENT-FAIL"
    assert "cam99" in questions[0].text or "isolated_dock" in questions[0].text

    # Verify caveat verdict was added to feasibility
    assert any(v.verdict == "caveat" for v in repaired_plan.feasibility)
    # The persistently failing rule was removed from active rules to preserve plan soundness
    assert not any(r.id == "R-PERSISTENT-FAIL" for r in repaired_plan.rules)
