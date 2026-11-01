"""Unit tests for Evaluation Runner E3 (task S-04 / MP §8 E3)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from eval.runners.e3 import (
    E3Summary,
    HeldoutRequest,
    RequestRunRecord,
    aggregate_e3,
    is_outside_author,
    load_heldout_requests,
    plan_for_request,
    render_e3_markdown_table,
    run_e3_evaluation,
    simulate_plan_for_request,
)

from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.plan import (
    AcceptanceCheck,
    AcceptanceResult,
    FeasibilityVerdict,
    SitePlan,
)
from rulecam.contracts.rule_ir import RuleIR

HELDOUT_FIXTURE = Path(__file__).resolve().parents[2] / "eval" / "heldout" / "requests.jsonl"


def test_load_heldout_requests_loads_twenty_requests() -> None:
    assert HELDOUT_FIXTURE.exists()
    requests = load_heldout_requests(HELDOUT_FIXTURE)
    assert len(requests) == 20
    assert all(isinstance(r, HeldoutRequest) for r in requests)
    assert {r.expected_outcome for r in requests} == {"observable", "not_observable", "declined"}


def test_simulate_plan_guardrail_decline() -> None:
    req = HeldoutRequest(
        id="test-decline",
        domain="warehouse",
        author="Manager",
        text="Tell me who is the slowest worker in the packaging line",
        expected_outcome="declined",
        expected_category="per_person_productivity",
    )
    plan, record = simulate_plan_for_request(req, run_idx=0)
    assert isinstance(plan, SitePlan)
    assert record.outcome == "declined"
    assert record.is_valid_or_honest is True
    assert record.first_pass_accepted is True
    assert len(plan.feasibility) == 1
    assert plan.feasibility[0].verdict == "declined"
    assert plan.feasibility[0].reason is not None
    assert plan.feasibility[0].alternative is not None


def test_simulate_plan_not_observable() -> None:
    req = HeldoutRequest(
        id="test-not-obs",
        domain="kitchen",
        author="Auditor",
        text="Check if chicken core temperature is 165 F",
        expected_outcome="not_observable",
        expected_category="not_observable",
    )
    plan, record = simulate_plan_for_request(req, run_idx=0)
    assert isinstance(plan, SitePlan)
    assert record.outcome == "not_observable"
    assert record.is_valid_or_honest is True
    assert len(plan.feasibility) == 1
    assert plan.feasibility[0].verdict == "not_observable"
    assert len(plan.feasibility[0].evidence) == 1


def test_simulate_plan_observable_valid_rule() -> None:
    req = HeldoutRequest(
        id="test-obs",
        domain="kitchen",
        author="Chef",
        text="Cooks must wash hands at sink before prep",
        expected_outcome="observable",
        expected_category="valid_rule",
    )
    plan, record = simulate_plan_for_request(req, run_idx=0)
    assert isinstance(plan, SitePlan)
    assert record.outcome == "valid_rule"
    assert record.is_valid_or_honest is True
    assert len(plan.rules) == 1
    assert plan.rules[0].zones["active_zone"].camera_id == "cam1"
    assert plan.acceptance.all_passed is True


def test_aggregate_e3_empty() -> None:
    summary = aggregate_e3([], total_requests=0, simulated=False)
    assert summary.total_runs == 0
    assert summary.valid_or_honest_rate == 0.0
    assert summary.gate_passed is False


def test_aggregate_e3_gate_pass() -> None:
    records = [
        RequestRunRecord(
            request_id=f"req-{i}",
            run_index=0,
            outcome="valid_rule",
            is_valid_or_honest=True,
            first_pass_accepted=True,
            repair_rounds=0,
            duration_s=1.0,
        )
        for i in range(10)
    ]
    # Calling aggregate_e3 directly without explicit outside requests fails by default
    default_summary = aggregate_e3(records, total_requests=10, simulated=False)
    assert default_summary.gate_passed is False
    assert default_summary.has_outside_requests is False
    assert default_summary.any_team_written is True

    # With outside requests explicitly confirmed, gate passes
    summary = aggregate_e3(
        records,
        total_requests=10,
        simulated=False,
        has_outside_requests=True,
        any_team_written=False,
    )
    assert summary.total_runs == 10
    assert summary.valid_or_honest_count == 10
    assert summary.valid_or_honest_rate == 1.0
    assert summary.first_pass_acceptance_rate == 1.0
    assert summary.simulated is False
    assert summary.has_outside_requests is True
    assert summary.any_team_written is False
    assert summary.gate_passed is True


def test_aggregate_e3_simulated_never_passes_gate() -> None:
    records = [
        RequestRunRecord(
            request_id=f"req-{i}",
            run_index=0,
            outcome="valid_rule",
            is_valid_or_honest=True,
            first_pass_accepted=True,
            repair_rounds=0,
            duration_s=0.0,
        )
        for i in range(10)
    ]
    summary = aggregate_e3(records, total_requests=10, simulated=True)
    assert summary.valid_or_honest_rate == 1.0
    assert summary.simulated is True
    assert summary.gate_passed is False


def test_render_e3_markdown_table() -> None:
    summary = E3Summary(
        total_requests=20,
        runs_per_request=3,
        total_runs=60,
        valid_or_honest_count=60,
        valid_or_honest_rate=1.0,
        first_pass_count=58,
        first_pass_acceptance_rate=0.9667,
        mean_repair_rounds=0.033,
        mean_duration_s=0.85,
        simulated=False,
        has_outside_requests=True,
        any_team_written=False,
        gate_passed=True,
    )
    md = render_e3_markdown_table(summary)
    assert "Evaluation E3: Held-Out Requests Generalisation" in md
    assert "100.0%" in md
    assert "PASS (Gate unblocked)" in md
    assert "SIMULATED" not in md


def test_render_e3_markdown_table_labels_simulated_runs() -> None:
    summary = E3Summary(
        total_requests=20,
        runs_per_request=3,
        total_runs=60,
        valid_or_honest_count=60,
        valid_or_honest_rate=1.0,
        first_pass_count=58,
        first_pass_acceptance_rate=0.9667,
        mean_repair_rounds=0.033,
        mean_duration_s=0.0,
        simulated=True,
        gate_passed=False,
    )
    md = render_e3_markdown_table(summary)
    assert "SIMULATED HARNESS, not a Planner run" in md
    assert "SIMULATED: no gate" in md
    assert "not evidence of generalisation" in md
    assert "PASS" not in md
    assert "Plan Generation Time" not in md


def test_run_e3_evaluation_end_to_end(tmp_path: Path) -> None:
    reqs_file = tmp_path / "requests.jsonl"
    reqs = [
        HeldoutRequest(
            id="req-1",
            domain="kitchen",
            author="Cook",
            text="Wash hands before prep",
            expected_outcome="observable",
            expected_category="valid_rule",
        ),
        HeldoutRequest(
            id="req-2",
            domain="kitchen",
            author="Owner",
            text="Rank workers by speed",
            expected_outcome="declined",
            expected_category="per_person_productivity",
        ),
    ]
    reqs_file.write_text("\n".join(r.model_dump_json() for r in reqs), encoding="utf-8")

    plans_dir = tmp_path / "plans"
    results_path = tmp_path / "results" / "e3.json"

    summary = run_e3_evaluation(
        reqs_file,
        plans_dir,
        results_path,
        runs_per_request=2,
    )

    assert summary.total_requests == 2
    assert summary.total_runs == 4
    assert summary.valid_or_honest_rate == 1.0
    assert summary.simulated is True
    assert summary.gate_passed is False
    assert summary.mean_duration_s == 0.0
    assert results_path.exists()
    assert len(list(plans_dir.glob("*.json"))) == 4
    assert len(list(plans_dir.glob("*.yaml"))) == 4


class FakePipeline:
    def __init__(self, plan_fn: Callable[[Any], SitePlan] | None = None) -> None:
        self._plan_fn = plan_fn

    def run(self, inputs: Any) -> SitePlan:
        if self._plan_fn:
            return self._plan_fn(inputs)
        raise RuntimeError("simulated pipeline failure")


def test_plan_for_request_guardrail_decline() -> None:
    req = HeldoutRequest(
        id="req-dec",
        domain="warehouse",
        author="Manager",
        text="Rank workers by speed",
        expected_outcome="declined",
        expected_category="per_person_productivity",
    )
    fake_pipe = FakePipeline()
    plan, record = plan_for_request(req, run_idx=0, pipeline=fake_pipe)  # type: ignore[arg-type]
    assert record.outcome == "declined"
    assert record.is_valid_or_honest is True
    assert record.first_pass_accepted is True
    assert record.duration_s >= 0.0
    assert len(plan.feasibility) == 1
    assert plan.feasibility[0].verdict == "declined"


def test_plan_for_request_real_pipeline_valid_rule() -> None:

    rule_yaml = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "rules"
        / "wash-before-line.rule.yaml"
    )
    raw_rule = yaml.safe_load(rule_yaml.read_text(encoding="utf-8"))

    def make_valid_plan(inputs: Any) -> SitePlan:
        rule = RuleIR.model_validate({**raw_rule, "id": f"rule-{inputs.plan_id}"})
        return SitePlan(
            plan_id=inputs.plan_id,
            site_name=inputs.site_name,
            setting_inferred="kitchen",
            languages=("en",),
            request=inputs.request,
            inventory=(),
            feasibility=(
                FeasibilityVerdict(
                    concern=inputs.request[0].text,
                    verdict="observable",
                    says="Observable on camera.",
                ),
            ),
            rules=(rule,),
            privacy_mode=PrivacyMode.STANDARD,
            acceptance=AcceptanceResult(
                checks=(AcceptanceCheck(item="rule", check="rule_valid", passed=True),),
                rounds=0,
            ),
        )

    req = HeldoutRequest(
        id="req-valid",
        domain="kitchen",
        author="Chef",
        text="Make sure staff wash hands for 20 seconds",
        expected_outcome="observable",
        expected_category="valid_rule",
    )
    fake_pipe = FakePipeline(make_valid_plan)
    _plan, record = plan_for_request(req, run_idx=0, pipeline=fake_pipe)  # type: ignore[arg-type]
    assert record.outcome == "valid_rule"
    assert record.is_valid_or_honest is True
    assert record.first_pass_accepted is True
    assert record.rule_ids == ["rule-plan-req-valid-r0"]
    assert record.duration_s >= 0.0


def test_plan_for_request_real_pipeline_failure() -> None:
    req = HeldoutRequest(
        id="req-fail",
        domain="kitchen",
        author="Chef",
        text="Make sure staff wash hands for 20 seconds",
        expected_outcome="observable",
        expected_category="valid_rule",
    )
    fake_pipe = FakePipeline()
    _plan, record = plan_for_request(req, run_idx=0, pipeline=fake_pipe)  # type: ignore[arg-type]
    assert record.outcome == "failed"
    assert record.is_valid_or_honest is False


def test_run_e3_evaluation_with_real_pipeline(tmp_path: Path) -> None:

    rule_yaml = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "rules"
        / "wash-before-line.rule.yaml"
    )
    raw_rule = yaml.safe_load(rule_yaml.read_text(encoding="utf-8"))

    def make_valid_plan(inputs: Any) -> SitePlan:
        rule = RuleIR.model_validate({**raw_rule, "id": f"rule-{inputs.plan_id}"})
        return SitePlan(
            plan_id=inputs.plan_id,
            site_name=inputs.site_name,
            setting_inferred="kitchen",
            languages=("en",),
            request=inputs.request,
            inventory=(),
            feasibility=(
                FeasibilityVerdict(
                    concern=inputs.request[0].text,
                    verdict="observable",
                    says="Observable on camera.",
                ),
            ),
            rules=(rule,),
            privacy_mode=PrivacyMode.STANDARD,
            acceptance=AcceptanceResult(
                checks=(AcceptanceCheck(item="rule", check="rule_valid", passed=True),),
                rounds=0,
            ),
        )

    reqs_file = tmp_path / "reqs.jsonl"
    reqs = [
        HeldoutRequest(
            id="req-1",
            domain="kitchen",
            author="outside: Chef",
            text="Make cooks wash hands for 20 seconds",
            expected_outcome="observable",
            expected_category="valid_rule",
        ),
        HeldoutRequest(
            id="req-2",
            domain="kitchen",
            author="outside: Owner",
            text="Rank workers by speed",
            expected_outcome="declined",
            expected_category="per_person_productivity",
        ),
    ]
    reqs_file.write_text("\n".join(r.model_dump_json() for r in reqs), encoding="utf-8")

    plans_dir = tmp_path / "plans"
    results_path = tmp_path / "results" / "e3.json"
    fake_pipe = FakePipeline(make_valid_plan)

    summary = run_e3_evaluation(
        reqs_file,
        plans_dir,
        results_path,
        runs_per_request=2,
        pipeline=fake_pipe,  # type: ignore[arg-type]
    )

    assert summary.total_requests == 2
    assert summary.total_runs == 4
    assert summary.valid_or_honest_rate == 1.0
    assert summary.simulated is False
    assert summary.gate_passed is True
    assert summary.mean_duration_s >= 0.0
    assert results_path.exists()


def test_plan_for_request_observable_declined_counts_as_miss() -> None:
    req = HeldoutRequest(
        id="req-obs-declined",
        domain="kitchen",
        author="Chef",
        text="Make sure staff wash hands for 20 seconds",
        expected_outcome="observable",
        expected_category="valid_rule",
    )

    def make_declined_plan(inputs: Any) -> SitePlan:
        return SitePlan(
            plan_id=inputs.plan_id,
            site_name=inputs.site_name,
            setting_inferred="kitchen",
            languages=("en",),
            request=inputs.request,
            inventory=(),
            feasibility=(
                FeasibilityVerdict(
                    concern=inputs.request[0].text,
                    verdict="declined",
                    says="Declined by policy.",
                    reason="custom policy",
                ),
            ),
            rules=(),
            privacy_mode=PrivacyMode.STANDARD,
            acceptance=AcceptanceResult(checks=(), rounds=0),
        )

    fake_pipe = FakePipeline(make_declined_plan)
    _plan, record = plan_for_request(req, run_idx=0, pipeline=fake_pipe)  # type: ignore[arg-type]
    assert record.outcome == "declined"
    assert record.is_valid_or_honest is False


def test_plan_for_request_observable_punted_not_observable_counts_as_miss() -> None:
    req = HeldoutRequest(
        id="req-obs-not-obs",
        domain="kitchen",
        author="Chef",
        text="Make sure staff wash hands for 20 seconds",
        expected_outcome="observable",
        expected_category="valid_rule",
    )

    def make_not_obs_plan(inputs: Any) -> SitePlan:
        return SitePlan(
            plan_id=inputs.plan_id,
            site_name=inputs.site_name,
            setting_inferred="kitchen",
            languages=("en",),
            request=inputs.request,
            inventory=(),
            feasibility=(
                FeasibilityVerdict(
                    concern=inputs.request[0].text,
                    verdict="not_observable",
                    says="Camera cannot see this.",
                ),
            ),
            rules=(),
            privacy_mode=PrivacyMode.STANDARD,
            acceptance=AcceptanceResult(checks=(), rounds=0),
        )

    fake_pipe = FakePipeline(make_not_obs_plan)
    _plan, record = plan_for_request(req, run_idx=0, pipeline=fake_pipe)  # type: ignore[arg-type]
    assert record.outcome == "not_observable"
    assert record.is_valid_or_honest is False


def test_plan_for_request_guardrail_decline_on_observable_counts_as_miss() -> None:
    # A request that triggers deterministic guardrail but was labeled observable is a miss
    req = HeldoutRequest(
        id="req-guardrail-mismatch",
        domain="warehouse",
        author="Auditor",
        text="Tell me who is the slowest worker in the packaging line",
        expected_outcome="observable",
        expected_category="per_person_productivity",
    )
    fake_pipe = FakePipeline()
    _plan, record = plan_for_request(req, run_idx=0, pipeline=fake_pipe)  # type: ignore[arg-type]
    assert record.outcome == "declined"
    assert record.is_valid_or_honest is False


def test_aggregate_e3_team_written_requests_never_pass_gate() -> None:
    records = [
        RequestRunRecord(
            request_id="req-1",
            run_index=0,
            outcome="valid_rule",
            is_valid_or_honest=True,
            first_pass_accepted=True,
            repair_rounds=0,
            duration_s=0.5,
        ),
    ]
    summary = aggregate_e3(
        records,
        total_requests=1,
        simulated=False,
        has_outside_requests=False,
        any_team_written=True,
    )
    assert summary.simulated is False
    assert summary.any_team_written is True
    assert summary.has_outside_requests is False
    assert summary.valid_or_honest_rate == 1.0
    assert summary.gate_passed is False

    md = render_e3_markdown_table(summary)
    assert "TEAM-WRITTEN" in md
    assert "BLOCKED: team-written requests" in md


def test_is_outside_author_validation() -> None:
    # Empty or whitespace
    assert is_outside_author("") is False
    assert is_outside_author("   ") is False
    assert is_outside_author(None) is False

    # Team / internal personas (various delimiters and cases)
    assert is_outside_author("team-written persona: warehouse manager") is False
    assert is_outside_author("team written persona: warehouse manager") is False
    assert is_outside_author("team_written persona: warehouse manager") is False
    assert is_outside_author("team\u2013written persona: warehouse manager") is False
    assert is_outside_author("internal persona: cook") is False
    assert is_outside_author("team persona: nurse") is False
    assert is_outside_author("persona: safety lead") is False

    # Unprefixed ordinary string without outside marker
    assert is_outside_author("Chef") is False
    assert is_outside_author("Manager") is False

    # Positive outside markers
    assert is_outside_author("outside: Jane Doe") is True
    assert is_outside_author("outsider: John Smith") is True
    assert is_outside_author("external: Food Safety Consultant") is True
    assert is_outside_author("@jane_safety_expert") is True


def test_run_e3_evaluation_with_team_written_requests_blocks_gate(tmp_path: Path) -> None:
    reqs_file = tmp_path / "team_reqs.jsonl"
    reqs = [
        HeldoutRequest(
            id="req-tw-1",
            domain="kitchen",
            author="team-written persona: chef",
            text="Staff must wear hairnets in prep area",
            expected_outcome="observable",
            expected_category="valid_rule",
        ),
        HeldoutRequest(
            id="req-tw-2",
            domain="kitchen",
            author="team written persona: auditor",
            text="Staff must wash hands for 20 seconds",
            expected_outcome="observable",
            expected_category="valid_rule",
        ),
    ]
    reqs_file.write_text("\n".join(r.model_dump_json() for r in reqs), encoding="utf-8")

    def make_valid_plan(inputs: Any) -> SitePlan:
        return SitePlan(
            plan_id=inputs.plan_id,
            site_name=inputs.site_name,
            setting_inferred="kitchen",
            languages=("en",),
            request=inputs.request,
            inventory=(),
            feasibility=(
                FeasibilityVerdict(
                    concern=inputs.request[0].text,
                    verdict="observable",
                    says="Observable.",
                ),
            ),
            rules=(
                RuleIR.model_validate(
                    {
                        "id": f"rule-{inputs.plan_id}",
                        "version": 1,
                        "source": {"kind": "plan", "plan_id": inputs.plan_id},
                        "concepts": {"worker": {"prompt": "person"}},
                        "zones": {
                            "active_zone": {
                                "kind": "polygon",
                                "camera_id": "cam1",
                                "polygon_px": [[0, 0], [10, 0], [10, 10], [0, 10]],
                            }
                        },
                        "predicate": {"op": "inside", "object": "worker", "zone": "active_zone"},
                        "severity": "medium",
                        "actions": [{"kind": "log"}],
                    }
                ),
            ),
            privacy_mode=PrivacyMode.STANDARD,
            acceptance=AcceptanceResult(
                checks=(AcceptanceCheck(item="rule", check="ok", passed=True),),
                rounds=0,
            ),
        )

    plans_dir = tmp_path / "plans"
    results_path = tmp_path / "results" / "e3.json"
    fake_pipe = FakePipeline(make_valid_plan)

    summary = run_e3_evaluation(
        reqs_file,
        plans_dir,
        results_path,
        runs_per_request=1,
        pipeline=fake_pipe,  # type: ignore[arg-type]
    )

    assert summary.simulated is False
    assert summary.has_outside_requests is False
    assert summary.any_team_written is True
    assert summary.team_written_count == 2
    assert summary.valid_or_honest_rate == 1.0
    assert summary.gate_passed is False

    md = render_e3_markdown_table(summary)
    assert "TEAM-WRITTEN" in md
    assert "BLOCKED: team-written requests" in md
    assert "*Team-written: all 2 requests are team-written personas." in md


def test_render_e3_markdown_table_repair_rounds_fail() -> None:
    records = [
        RequestRunRecord(
            request_id="req-repair",
            run_index=0,
            outcome="valid_rule",
            is_valid_or_honest=True,
            first_pass_accepted=False,
            repair_rounds=2,
            duration_s=1.0,
        )
    ]
    summary = aggregate_e3(
        records,
        total_requests=1,
        simulated=False,
        has_outside_requests=True,
        any_team_written=False,
    )
    assert summary.mean_repair_rounds == 2.0
    md = render_e3_markdown_table(summary)
    assert r"| **Mean Repair Rounds** | $\le 1.0$ | 2.00 rounds | FAIL |" in md
