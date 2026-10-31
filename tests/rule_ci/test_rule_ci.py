"""Unit tests for Rule CI boundary-trace fuzzing and mutation testing (task P2-08)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from rulecam.bus import Bus, Message, Topic
from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.plan import AcceptanceResult, SitePlan
from rulecam.contracts.rule_ir import RuleIR
from rulecam.rule_ci.models import RuleCIReport
from rulecam.rule_ci.mutations import generate_mutations
from rulecam.rule_ci.runner import prove_plan_hook, run_rule_ci
from rulecam.rule_ci.traces import generate_rule_traces, is_rule_vacuous

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _load_rule(filename: str) -> RuleIR:
    raw = yaml.safe_load((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    return RuleIR.model_validate(raw)


def test_handwash_passes_boundary_traces() -> None:
    """The handwash rule passes its boundary traces (MP §7.3)."""
    rule = _load_rule("wash-before-line.rule.yaml")
    traces = generate_rule_traces(rule)

    assert len(traces) >= 3

    # Check MP §7.3 canonical boundary traces exist
    names = [t.name for t in traces]
    assert "22s_wash_passes" in names
    assert "8s_wash_fails" in names
    assert "dwell_19_5s_violation" in names
    assert "dwell_20_5s_pass" in names

    report = run_rule_ci(rule, traces=traces)
    assert report.traces_total == len(traces)
    assert report.traces_passed == report.traces_total
    assert report.nonvacuous is True


def test_handwash_mutants_killed() -> None:
    """All standard mutants of the handwash rule are killed."""
    rule = _load_rule("wash-before-line.rule.yaml")
    mutants = generate_mutations(rule)

    assert len(mutants) >= 5

    mutant_ops = [m.operator for m in mutants]
    assert "scale_threshold_down" in mutant_ops
    assert "scale_threshold_up" in mutant_ops
    assert "switch_temporal_direction" in mutant_ops

    report = run_rule_ci(rule, mutants=mutants)
    assert report.mutants_killed == report.mutants_total
    assert report.ci_green is True


def test_distance_rule_mutants_killed() -> None:
    """All standard mutants of the forklift distance rule are killed."""
    rule = _load_rule("forklift-proximity.rule.yaml")
    traces = generate_rule_traces(rule)
    mutants = generate_mutations(rule)

    assert len(traces) >= 2
    assert len(mutants) >= 3

    report = run_rule_ci(rule, traces=traces, mutants=mutants)
    assert report.traces_passed == report.traces_total
    assert report.mutants_killed == report.mutants_total
    assert report.ci_green is True


def test_vacuous_rule_flagged_nonvacuous_false() -> None:
    """A deliberately vacuous rule (threshold 0) is flagged nonvacuous=false."""
    rule = _load_rule("wash-before-line.rule.yaml")
    rule_dict = rule.model_dump(mode="json")
    # Mutate dwell threshold to 0.0 s
    rule_dict["predicate"]["requirement"]["total_s_gte"] = 0.0
    vacuous_rule = RuleIR.model_validate(rule_dict)

    assert is_rule_vacuous(vacuous_rule) is True

    report = run_rule_ci(vacuous_rule)
    assert report.nonvacuous is False
    assert report.ci_green is False
    assert any("nonvacuous=False" in f for f in report.failures)


def test_rule_ci_report_schema_and_serialization() -> None:
    """Test RuleCIReport validates schema and model properties."""
    report = RuleCIReport(
        rule_id="R-TEST",
        traces_passed=5,
        traces_total=5,
        mutants_killed=9,
        mutants_total=10,
        failures=[],
        nonvacuous=True,
    )
    assert report.ci_green is True

    dumped = report.model_dump()
    loaded = RuleCIReport.model_validate(dumped)
    assert loaded == report

    # Test failure when traces fail
    report_failed_trace = report.model_copy(update={"traces_passed": 4})
    assert report_failed_trace.ci_green is False

    # Test failure when mutant kill rate < 90% without justification
    report_low_mutants = report.model_copy(update={"mutants_killed": 8})
    assert report_low_mutants.ci_green is False

    # Test passing when mutant kill rate < 90% with recorded justification
    report_justified = report_low_mutants.model_copy(
        update={"justification": "Edge-case threshold equivalent in this camera geometry"}
    )
    assert report_justified.ci_green is True


def test_run_rule_ci_publishes_to_bus() -> None:
    """Test run_rule_ci publishes result to Topic.CI_RESULT."""
    bus = Bus()
    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus._queues[Topic.CI_RESULT].append(queue)

    rule = _load_rule("wash-before-line.rule.yaml")
    report = run_rule_ci(rule, bus=bus)

    assert queue.qsize() == 1
    msg = queue.get_nowait()
    event = msg.payload
    assert isinstance(event, dict)
    assert event["rule_id"] == rule.id
    assert event["report"]["rule_id"] == rule.id
    assert event["report"]["traces_total"] == report.traces_total


def test_prove_plan_hook_executes_for_all_rules() -> None:
    """Test prove_plan_hook executes Rule CI on all rules in a SitePlan."""
    bus = Bus()
    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus._queues[Topic.CI_RESULT].append(queue)

    rule1 = _load_rule("wash-before-line.rule.yaml")
    rule2 = _load_rule("forklift-proximity.rule.yaml")
    plan = SitePlan(
        plan_id="plan-prove-test",
        version=1,
        site_name="Test Warehouse",
        setting_inferred="warehouse",
        languages=("en",),
        request=(),
        inventory=(),
        feasibility=(),
        rules=(rule1, rule2),
        privacy_mode=PrivacyMode.STANDARD,
        retention_days=30,
        protected_two_key=(),
        roles={},
        acceptance=AcceptanceResult(checks=(), rounds=0),
        audit=(),
        back_translation="",
        standards=(),
        cost_usd_per_cam_hour=None,
    )

    hook = prove_plan_hook(bus=bus)
    returned_plan = hook(plan)
    assert returned_plan == plan
    assert queue.qsize() == 2
    events = [queue.get_nowait().payload for _ in range(2)]
    assert {e["rule_id"] for e in events if isinstance(e, dict)} == {rule1.id, rule2.id}
