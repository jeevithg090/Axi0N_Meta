"""Rule CI kinematic (TTC) and operator-vs-pedestrian traces (WS1c, WS1d; P2-08)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from rulecam.contracts.rule_ir import RuleIR
from rulecam.rule_ci import generate_mutations, generate_rule_traces, run_rule_ci
from rulecam.rule_ci.runner import fires
from rulecam.rules.near_miss import CONTACT_M

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rules"
DISTANCE = {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0}
MOVING = {"op": "moving", "object": "forklift", "speed_mps_gt": 0.2}


def _rule(predicate: dict[str, Any], concepts: dict[str, str] | None = None) -> RuleIR:
    raw: dict[str, Any] = yaml.safe_load(
        (FIXTURES / "forklift-proximity.rule.yaml").read_text("utf-8")
    )
    raw["predicate"] = predicate
    if concepts is not None:
        raw["concepts"] = {key: {"prompt": prompt} for key, prompt in concepts.items()}
    return RuleIR.model_validate(raw)


def _traces(rule: RuleIR) -> dict[str, Any]:
    return {t.name: t for t in generate_rule_traces(rule)}


def test_the_proximity_rule_gets_the_kinematic_and_operator_traces() -> None:
    traces = _traces(_rule({"op": "all", "of": [DISTANCE, MOVING]}))
    expected = {
        "approach_lt_1.95m": True,
        "approach_lt_2.05m": False,
        "pass_lt_1.95m": True,
        "pass_lt_2.05m": False,
        "co_moving_far_6.4m": False,
        "operator_riding_0.5m": False,
        "pedestrian_beside_0.5m": True,
        "pedestrian_in_front_0.5m": True,
        "operator_riding_slow_0.5m": False,
        "pedestrian_slow_approach_0.5m": True,
    }
    assert {name: traces[name].expected_violation for name in expected} == expected
    rule = _rule({"op": "all", "of": [DISTANCE, MOVING]})
    assert {name: fires(rule, traces[name]) for name in expected} == expected


def test_the_live_far_candidate_is_a_trace_that_kills_an_inverted_bound() -> None:
    """WS1d: closest_m 6.4 m, ttc none, against a 2 m rule must stay quiet; `gt` fires on it."""
    rule = _rule({"op": "all", "of": [DISTANCE, MOVING]})
    far = _traces(rule)["co_moving_far_6.4m"]
    inverted = _rule({"op": "all", "of": [{**DISTANCE, "lt": None, "gt": 2.0}, MOVING]})
    assert fires(rule, far) is False
    assert fires(inverted, far) is True
    flip = next(m for m in generate_mutations(rule) if m.name == "flip_lt_gt")
    assert fires(flip.rule, far) is True


def test_the_riding_trace_kills_the_drop_operator_exclusion_mutant() -> None:
    rule = _rule({"op": "all", "of": [DISTANCE, MOVING]})
    riding = _traces(rule)["operator_riding_0.5m"]
    assert fires(rule, riding) is False
    assert fires(rule, riding, operator_exclusion=False) is True
    report = run_rule_ci(rule)
    assert report.ci_green, report.failures
    assert report.mutants_killed == report.mutants_total


def test_approach_traces_close_to_the_threshold_with_a_collision_course() -> None:
    rule = _rule({"op": "all", "of": [DISTANCE, MOVING]})
    trace = _traces(rule)["approach_lt_1.95m"]
    person, forklift = trace.tracks
    gaps = [
        abs(p.foot_px[0] - (f.foot_px[0] + 20.0)) / 100.0  # 100 px = 1 m; footprint half 0.2 m
        for p, f in zip(person.points, forklift.points, strict=True)
    ]
    assert min(gaps) == pytest.approx(1.95)
    assert gaps[0] > 2.0 + CONTACT_M


@pytest.mark.parametrize(
    "predicate",
    [
        DISTANCE,
        {"op": "all", "of": [DISTANCE, {**MOVING, "object": "person"}]},
        {"op": "persist", "seconds": 1.0, "of": {"op": "all", "of": [DISTANCE, MOVING]}},
        {"op": "all", "of": [{**DISTANCE, "lt": 0.3}, {**MOVING, "speed_mps_gt": 2.5}]},
        {"op": "all", "of": [{**DISTANCE, "lt": 6.0}, MOVING]},
    ],
    ids=["alone", "moving-subject", "persist", "tight-fast", "wide"],
)
def test_every_separation_shape_is_green_with_all_mutants_killed(
    predicate: dict[str, Any],
) -> None:
    report = run_rule_ci(_rule(predicate))
    assert report.ci_green, report.failures
    assert report.nonvacuous
    assert report.mutants_killed == report.mutants_total


def test_persist_skips_the_pairs_and_moving_subject_skips_the_in_front_trace() -> None:
    persist = _traces(
        _rule({"op": "persist", "seconds": 1.0, "of": {"op": "all", "of": [DISTANCE, MOVING]}})
    )
    assert "approach_lt_1.95m" not in persist
    assert "co_moving_far_6.4m" in persist
    assert "operator_riding_0.5m" in persist
    subject = _traces(_rule({"op": "all", "of": [DISTANCE, {**MOVING, "object": "person"}]}))
    assert "pedestrian_in_front_0.5m" not in subject
    assert "pedestrian_beside_0.5m" in subject


def test_operator_traces_only_for_person_vehicle_footprint_rules() -> None:
    bucket = _rule(
        {"op": "distance_m", "a": "person", "b": "bucket", "lt": 2.0},
        concepts={"person": "person", "bucket": "excavator bucket"},
    )
    names = set(_traces(bucket))
    assert "co_moving_far_6.4m" in names
    assert not any(n.startswith(("operator_", "pedestrian_")) for n in names)
    assert "drop_operator_exclusion" not in {m.name for m in generate_mutations(bucket)}
    foot = _traces(_rule({**DISTANCE, "b": "forklift.foot_point"}))
    assert "approach_lt_1.95m" in foot
    assert not any(n.startswith("operator_") for n in foot)


def test_a_gt_bound_gets_no_kinematic_traces() -> None:
    names = set(_traces(_rule({**DISTANCE, "lt": None, "gt": 2.0})))
    assert not any(n.startswith(("approach_", "pass_", "co_moving_", "operator_")) for n in names)
