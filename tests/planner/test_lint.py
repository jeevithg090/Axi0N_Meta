"""Unit tests for the Plan and Rule IR linter (task P2-06b, MP §3.2, §7.2).

Verifies that each of the 8 deterministic lint error classes is caught:
  1. undefined_concept
  2. undefined_zone
  3. zone_out_of_view
  4. unreasonable_threshold (distance < 0.3m / > 20m, duration < 1s / > 3600s)
  5. contradictory_rules (intra-rule and inter-rule contradictions)
  6. privacy_inconsistency (Anonymous Mode with facial/biometric recognition)
  7. private_space_zone (restroom, bathroom, locker room)
  8. missing_action and missing_severity
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.plan import AcceptanceResult, InventoryItem, RequestItem, SitePlan
from rulecam.contracts.rule_ir import (
    All,
    ConceptSpec,
    DistanceM,
    Dwell,
    Inside,
    Not_,
    Persist,
    RuleIR,
    ZoneSpec,
)
from rulecam.contracts.rule_ir import (
    Sequence as RuleSequence,
)
from rulecam.planner.lint import (
    ContradictoryRulesError,
    MissingActionError,
    MissingSeverityError,
    PrivacyInconsistencyError,
    PrivateSpaceZoneError,
    UndefinedConceptError,
    UndefinedZoneError,
    UnreasonableThresholdError,
    ZoneOutOfViewError,
    extract_concept_references,
    extract_zone_references,
    lint_plan,
    lint_plan_or_raise,
    lint_rule,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _make_valid_rule() -> RuleIR:
    """Load canonical valid handwash rule fixture."""
    raw = yaml.safe_load((FIXTURES_DIR / "wash-before-line.rule.yaml").read_text(encoding="utf-8"))
    return RuleIR.model_validate(raw)


def _make_valid_plan(rule: RuleIR | None = None) -> SitePlan:
    """Create a minimal valid SitePlan with inventory and rules."""
    active_rule = rule or _make_valid_rule()
    return SitePlan(
        plan_id="plan_lint_valid",
        version=1,
        site_name="Test Kitchen",
        setting_inferred="commercial_kitchen",
        languages=("en",),
        request=(RequestItem(kind="text", text="Wash hands before prep"),),
        inventory=(
            InventoryItem(concept="person", camera_id="cam1", count=2, mean_confidence=0.9),
            InventoryItem(concept="hands", camera_id="cam1", count=2, mean_confidence=0.88),
            InventoryItem(concept="sink", camera_id="cam1", count=1, mean_confidence=0.95),
        ),
        feasibility=(),
        rules=(active_rule,),
        privacy_mode=PrivacyMode.STANDARD,
        retention_days=30,
        protected_two_key=(),
        roles={"shift_lead": "web_push"},
        acceptance=AcceptanceResult(checks=(), rounds=0),
        standards=(),
    )


def test_valid_plan_passes_linter() -> None:
    """A valid plan and rule produce zero lint findings."""
    plan = _make_valid_plan()
    errors = lint_plan(plan, available_cameras=["cam1"])
    assert errors == []
    # Verify or_raise does not throw
    lint_plan_or_raise(plan, available_cameras=["cam1"])


def test_lint_error_1_undefined_concept() -> None:
    """Linter flags predicate referencing a concept not declared in rule or inventory."""
    base = _make_valid_rule()
    bad_rule = base.model_copy(
        update={
            "predicate": Inside(object="forklift", zone="line_entry"),
        }
    )
    errors = lint_rule(bad_rule)
    assert any(isinstance(e, UndefinedConceptError) for e in errors)
    err = next(e for e in errors if isinstance(e, UndefinedConceptError))
    assert "forklift" in str(err)


def test_lint_error_2_undefined_zone() -> None:
    """Linter flags predicate referencing a zone not declared in rule or plan."""
    base = _make_valid_rule()
    bad_rule = base.model_copy(
        update={
            "predicate": Inside(object="person", zone="nonexistent_loading_dock"),
        }
    )
    errors = lint_rule(bad_rule)
    assert any(isinstance(e, UndefinedZoneError) for e in errors)
    err = next(e for e in errors if isinstance(e, UndefinedZoneError))
    assert "nonexistent_loading_dock" in str(err)


def test_lint_error_3_zone_out_of_view() -> None:
    """Linter flags zone assigned to a camera ID not in active camera views."""
    base = _make_valid_rule()
    errors = lint_rule(base, available_cameras=["cam9"])
    assert any(isinstance(e, ZoneOutOfViewError) for e in errors)
    err = next(e for e in errors if isinstance(e, ZoneOutOfViewError))
    assert "cam1" in str(err)


def test_lint_error_4_unreasonable_distance_threshold() -> None:
    """Linter flags ground distance thresholds outside [0.3, 20.0] metres."""
    base = _make_valid_rule()

    # Too small: 0.1m (< 0.3m)
    bad_dist_small = base.model_copy(
        update={
            "predicate": DistanceM(a="person", b="sink", lt=0.1),
        }
    )
    errors_small = lint_rule(bad_dist_small)
    assert any(isinstance(e, UnreasonableThresholdError) for e in errors_small)

    # Too large: 25.0m (> 20.0m)
    bad_dist_large = base.model_copy(
        update={
            "predicate": DistanceM(a="person", b="sink", gt=25.0),
        }
    )
    errors_large = lint_rule(bad_dist_large)
    assert any(isinstance(e, UnreasonableThresholdError) for e in errors_large)


def test_lint_error_4_unreasonable_duration_threshold() -> None:
    """Linter flags temporal thresholds outside [1.0, 3600.0] seconds."""
    base = _make_valid_rule()

    # Persist too short (0.2s < 1.0s)
    bad_persist = base.model_copy(
        update={
            "predicate": Persist(of=Inside(object="person", zone="line_entry"), seconds=0.2),
        }
    )
    errors = lint_rule(bad_persist)
    assert any(isinstance(e, UnreasonableThresholdError) for e in errors)

    # Dwell total_s_gte too long (5000s > 3600s)
    bad_dwell = base.model_copy(
        update={
            "predicate": Dwell(object="person", zone="line_entry", total_s_gte=5000.0),
        }
    )
    errors_dwell = lint_rule(bad_dwell)
    assert any(isinstance(e, UnreasonableThresholdError) for e in errors_dwell)


def test_lint_error_5_contradictory_rules_intra_rule() -> None:
    """Linter flags single rule having contradictory clauses in All([A, Not_(A)])."""
    base = _make_valid_rule()
    bad_rule = base.model_copy(
        update={
            "predicate": All(
                of=(
                    Inside(object="person", zone="line_entry"),
                    Not_(of=Inside(object="person", zone="line_entry")),
                )
            ),
        }
    )
    errors = lint_rule(bad_rule)
    assert any(isinstance(e, ContradictoryRulesError) for e in errors)


def test_lint_error_5_contradictory_rules_inter_rule() -> None:
    """Linter flags two rules with opposite requirements for the same subject and zone."""
    base = _make_valid_rule()
    rule_a = base.model_copy(
        update={
            "id": "rule_a",
            "predicate": Inside(object="person", zone="line_entry"),
        }
    )
    rule_b = base.model_copy(
        update={
            "id": "rule_b",
            "predicate": Not_(of=Inside(object="person", zone="line_entry")),
        }
    )
    plan = _make_valid_plan(rule=rule_a).model_copy(update={"rules": (rule_a, rule_b)})
    errors = lint_plan(plan, available_cameras=["cam1"])
    assert any(isinstance(e, ContradictoryRulesError) for e in errors)


def test_lint_error_6_privacy_inconsistency() -> None:
    """Linter flags facial or individual biometric recognition under Anonymous Mode."""
    base = _make_valid_rule()
    bad_rule = base.model_copy(
        update={
            "concepts": {
                "person": ConceptSpec(prompt="person"),
                "unmasked_face": ConceptSpec(prompt="unmasked_face"),
            },
            "predicate": Inside(object="unmasked_face", zone="line_entry"),
        }
    )
    errors = lint_rule(bad_rule, privacy_mode=PrivacyMode.ANONYMOUS)
    assert any(isinstance(e, PrivacyInconsistencyError) for e in errors)


def test_lint_error_7_private_space_zone() -> None:
    """Linter flags zones defined in strictly private areas (restroom, breakroom)."""
    base = _make_valid_rule()
    bad_zones = dict(base.zones)
    bad_zones["employee_restroom"] = ZoneSpec(
        kind="polygon",
        camera_id="cam1",
        polygon_px=((0, 0), (10, 0), (10, 10), (0, 10)),
    )
    bad_rule = base.model_copy(update={"zones": bad_zones})
    errors = lint_rule(bad_rule)
    assert any(isinstance(e, PrivateSpaceZoneError) for e in errors)
    err = next(e for e in errors if isinstance(e, PrivateSpaceZoneError))
    assert "employee_restroom" in str(err)


def test_lint_error_8_missing_actions() -> None:
    """Linter flags rules with empty action list."""
    base = _make_valid_rule()
    bad_rule = base.model_copy(update={"actions": ()})
    errors = lint_rule(bad_rule)
    assert any(isinstance(e, MissingActionError) for e in errors)


def test_lint_error_8_missing_severity() -> None:
    """Linter flags rules missing severity rating."""
    base = _make_valid_rule()
    bad_rule = base.model_copy(update={"severity": ""})
    errors = lint_rule(bad_rule)
    assert any(isinstance(e, MissingSeverityError) for e in errors)


def test_lint_plan_or_raise_throws_first_error() -> None:
    """lint_plan_or_raise raises the first discovered LintError."""
    base = _make_valid_rule()
    bad_rule = base.model_copy(update={"actions": ()})
    plan = _make_valid_plan(bad_rule)

    with pytest.raises(MissingActionError):
        lint_plan_or_raise(plan)


def test_extract_concept_and_zone_references_complex() -> None:
    """Test recursive extraction of concepts and zones across nested predicates."""
    complex_pred = RuleSequence(
        semantics="past",
        trigger=Inside(object="person.foot_point", zone="zone_gate"),
        require_within_s=120.0,
        requirement=Dwell(object="person.hands", zone="zone_sanitizer", total_s_gte=20.0),
    )
    concepts = extract_concept_references(complex_pred)
    assert concepts == {"person"}

    zones = extract_zone_references(complex_pred)
    assert zones == {"zone_gate", "zone_sanitizer"}
