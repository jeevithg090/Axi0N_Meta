"""Evaluation runner E3: Held-out requests generalisation (task book §4.3, MP §8 E3).

E3 asks whether the Planner turns every concern in a held-out request into a valid RuleIR, an
honest guardrail decline, or an honest not_observable verdict (target: >= 80% valid-or-honest).

This runner supports both real PlannerPipeline execution (`run_e3_evaluation(pipeline=...)`)
and a simulated harness (`simulate_plan_for_request`) for smoke-testing aggregation and reporting.
When run without a pipeline, summaries are marked `simulated=True` and cannot pass the E3 gate.

Outcomes are strictly checked against each request's `expected_outcome` label:
- `observable`: must produce an accepted, valid RuleIR. Punting or declining counts as a miss.
- `not_observable`: must produce an honest non-observable feasibility verdict with no rules.
- `declined`: must produce an honest guardrail decline with reason and alternative.

Furthermore, passing the E3 evaluation gate (`gate_passed=True`) requires:
1. `simulated=False` (real PlannerPipeline run).
2. Held-out requests authored by people outside the team (no `team-written` authors; S-04 / W-10).
3. Achieved valid-or-honest rate >= 80.0%.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, Field

from rulecam.contracts.core import PrivacyMode, Severity
from rulecam.contracts.plan import (
    AcceptanceCheck,
    AcceptanceResult,
    FeasibilityVerdict,
    Probe,
    RequestItem,
    SitePlan,
)
from rulecam.contracts.rule_ir import RuleIR
from rulecam.log import get_logger
from rulecam.planner.guardrails import Declined, check_request
from rulecam.planner.pipeline import PipelineInput, PlannerPipeline, plan_to_yaml

_log = get_logger(__name__)

TARGET_VALID_OR_HONEST_RATE: Final = 0.80
TARGET_MEAN_REPAIR_ROUNDS: Final = 1.0
DEFAULT_RUNS_PER_REQUEST: Final = 3
REPAIR_STRESS_RUN_IDX: Final = 2
POSITIVE_OUTSIDE_PREFIXES: Final = ("outside:", "outsider:", "external:")
BANNED_TEAM_INDICATORS: Final = (
    "team-written",
    "team written",
    "team_written",
    "team–written",  # noqa: RUF001
    "team—written",
    "internal persona",
    "team persona",
    "persona:",
)


def is_outside_author(author: str | None) -> bool:
    """Return True only if an author has a positive outside marker and no team indicators."""
    if not author or not author.strip():
        return False
    cleaned = author.strip().lower()
    if any(banned in cleaned for banned in BANNED_TEAM_INDICATORS):
        return False
    return any(cleaned.startswith(p) for p in POSITIVE_OUTSIDE_PREFIXES) or cleaned.startswith("@")


class HeldoutRequest(BaseModel):
    """A held-out evaluation request from an outside author."""

    id: str
    domain: str
    author: str
    text: str
    expected_outcome: Literal["observable", "not_observable", "declined"]
    expected_category: str


class RequestRunRecord(BaseModel):
    """Result of running one held-out request."""

    request_id: str
    run_index: int
    outcome: Literal["valid_rule", "declined", "not_observable", "failed"]
    is_valid_or_honest: bool
    first_pass_accepted: bool
    repair_rounds: int
    duration_s: float
    rule_ids: list[str] = Field(default_factory=list)
    declined_reason: str | None = None
    not_observable_reason: str | None = None


class E3Summary(BaseModel):
    """Aggregated E3 evaluation results across all runs."""

    total_requests: int
    runs_per_request: int
    total_runs: int
    valid_or_honest_count: int
    valid_or_honest_rate: float
    first_pass_count: int
    first_pass_acceptance_rate: float
    mean_repair_rounds: float
    mean_duration_s: float
    simulated: bool
    has_outside_requests: bool = False
    any_team_written: bool = True
    team_written_count: int = 0
    gate_passed: bool
    runs: list[RequestRunRecord] = Field(default_factory=list)


def load_heldout_requests(path: Path) -> list[HeldoutRequest]:
    """Load held-out evaluation requests from a JSONL file."""
    requests: list[HeldoutRequest] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            requests.append(HeldoutRequest.model_validate_json(line))
    return requests


def simulate_plan_for_request(
    request: HeldoutRequest,
    run_idx: int,
) -> tuple[SitePlan, RequestRunRecord]:
    """Stand in for the Planner on one held-out request; the Planner is never called.

    Outcomes are fixed by the guardrail and the request's own label, durations are 0.0 because
    nothing is timed, and one repair round is forced on two requests so the aggregation of repair
    counts is exercised.
    """
    guardrail = check_request(request.text)
    plan_id = f"plan-{request.id}-r{run_idx}"
    site_name = f"Site {request.domain.replace('_', ' ').title()}"

    if isinstance(guardrail, Declined):
        verdict = FeasibilityVerdict(
            concern=request.text,
            verdict="declined",
            says=f"I cannot accept this request: {guardrail.reason}",
            reason=guardrail.reason,
            alternative=guardrail.alternative,
        )
        plan = SitePlan(
            plan_id=plan_id,
            site_name=site_name,
            setting_inferred=request.domain,
            languages=("en",),
            request=(RequestItem(kind="text", text=request.text),),
            inventory=(),
            feasibility=(verdict,),
            rules=(),
            privacy_mode=PrivacyMode.STANDARD,
            acceptance=AcceptanceResult(
                checks=(
                    AcceptanceCheck(
                        item="feasibility",
                        check="guardrail_decline_valid",
                        passed=True,
                        detail="declined with reason and alternative",
                    ),
                ),
                rounds=0,
            ),
        )
        record = RequestRunRecord(
            request_id=request.id,
            run_index=run_idx,
            outcome="declined",
            is_valid_or_honest=True,
            first_pass_accepted=True,
            repair_rounds=0,
            duration_s=0.0,
            declined_reason=guardrail.reason,
        )
        return plan, record

    if request.expected_outcome == "not_observable":
        probe = Probe(
            concept="internal_sensor_or_hidden_state",
            camera_id="cam1",
            n_frames=30,
            detect_rate=0.0,
            detect_lb=0.0,
            median_px_height=0.0,
        )
        verdict = FeasibilityVerdict(
            concern=request.text,
            verdict="not_observable",
            says="I cannot verify internal temperature or hidden contents with optical cameras.",
            evidence=(probe,),
            alternative="Use physical IoT probes or direct manifest checks instead.",
        )
        plan = SitePlan(
            plan_id=plan_id,
            site_name=site_name,
            setting_inferred=request.domain,
            languages=("en",),
            request=(RequestItem(kind="text", text=request.text),),
            inventory=(),
            feasibility=(verdict,),
            rules=(),
            privacy_mode=PrivacyMode.STANDARD,
            acceptance=AcceptanceResult(
                checks=(
                    AcceptanceCheck(
                        item="feasibility",
                        check="observability_honest",
                        passed=True,
                        detail="optical camera physical boundary honest verdict",
                    ),
                ),
                rounds=0,
            ),
        )
        record = RequestRunRecord(
            request_id=request.id,
            run_index=run_idx,
            outcome="not_observable",
            is_valid_or_honest=True,
            first_pass_accepted=True,
            repair_rounds=0,
            duration_s=0.0,
            not_observable_reason=verdict.says,
        )
        return plan, record

    rule_id = f"rule-{request.id}"
    verdict = FeasibilityVerdict(
        concern=request.text,
        verdict="observable",
        says="Observable on primary station camera.",
    )
    standards = ["OSHA 1910"] if "warehouse" in request.domain else ["FDA Food Code §2-301.12"]
    rule = RuleIR.model_validate(
        {
            "id": rule_id,
            "version": 1,
            "source": {
                "kind": "plan",
                "plan_id": plan_id,
                "standard": standards,
            },
            "concepts": {
                "worker": {"prompt": "person"},
                "safety_equipment": {"prompt": "safety equipment", "static": False},
            },
            "zones": {
                "active_zone": {
                    "kind": "polygon",
                    "camera_id": "cam1",
                    "polygon_px": [[0, 0], [400, 0], [400, 300], [0, 300]],
                }
            },
            "predicate": {
                "op": "inside",
                "object": "worker",
                "zone": "active_zone",
            },
            "severity": Severity.HIGH,
            "actions": [
                {"kind": "log"},
                {"kind": "notify", "role": "shift_lead"},
            ],
            "verify": {"tier1": "glimmer", "tier2": "spark-open-on-dispute"},
            "accept": ["zone_in_view", "detect_lb>=0.8", "ci_green"],
            "backtestable": "yes",
        }
    )
    needs_repair = run_idx == REPAIR_STRESS_RUN_IDX and request.id in {"heldout-09", "heldout-14"}
    repair_rounds = 1 if needs_repair else 0
    checks = (
        AcceptanceCheck(
            item=rule_id,
            check="linter_clean",
            passed=True,
            detail="no linter errors",
        ),
        AcceptanceCheck(
            item=rule_id,
            check="camera_resolved",
            passed=True,
            detail="cam1 assigned",
        ),
    )
    plan = SitePlan(
        plan_id=plan_id,
        site_name=site_name,
        setting_inferred=request.domain,
        languages=("en",),
        request=(RequestItem(kind="text", text=request.text),),
        inventory=(),
        feasibility=(verdict,),
        rules=(rule,),
        privacy_mode=PrivacyMode.STANDARD,
        acceptance=AcceptanceResult(checks=checks, rounds=repair_rounds),
    )
    record = RequestRunRecord(
        request_id=request.id,
        run_index=run_idx,
        outcome="valid_rule",
        is_valid_or_honest=True,
        first_pass_accepted=(repair_rounds == 0),
        repair_rounds=repair_rounds,
        duration_s=0.0,
        rule_ids=[rule_id],
    )
    return plan, record


def plan_for_request(
    request: HeldoutRequest,
    run_idx: int,
    pipeline: PlannerPipeline,
    *,
    camera_frames: dict[str, tuple[Path, ...]] | None = None,
) -> tuple[SitePlan, RequestRunRecord]:
    """Execute the real PlannerPipeline for one held-out request, timing its run."""
    t0 = time.monotonic()
    plan_id = f"plan-{request.id}-r{run_idx}"
    site_name = f"Site {request.domain.replace('_', ' ').title()}"

    guardrail = check_request(request.text)
    if isinstance(guardrail, Declined):
        verdict = FeasibilityVerdict(
            concern=request.text,
            verdict="declined",
            says=f"I cannot accept this request: {guardrail.reason}",
            reason=guardrail.reason,
            alternative=guardrail.alternative,
        )
        plan = SitePlan(
            plan_id=plan_id,
            site_name=site_name,
            setting_inferred=request.domain,
            languages=("en",),
            request=(RequestItem(kind="text", text=request.text),),
            inventory=(),
            feasibility=(verdict,),
            rules=(),
            privacy_mode=PrivacyMode.STANDARD,
            acceptance=AcceptanceResult(
                checks=(
                    AcceptanceCheck(
                        item="feasibility",
                        check="guardrail_decline_valid",
                        passed=True,
                        detail="declined with reason and alternative",
                    ),
                ),
                rounds=0,
            ),
        )
        duration_s = max(0.001, time.monotonic() - t0)
        is_honest = request.expected_outcome == "declined"
        record = RequestRunRecord(
            request_id=request.id,
            run_index=run_idx,
            outcome="declined",
            is_valid_or_honest=is_honest,
            first_pass_accepted=True,
            repair_rounds=0,
            duration_s=round(duration_s, 3),
            declined_reason=guardrail.reason,
        )
        return plan, record

    inputs = PipelineInput(
        plan_id=plan_id,
        site_name=site_name,
        request=(RequestItem(kind="text", text=request.text),),
        camera_frames=camera_frames or {},
        languages=("en",),
    )

    try:
        plan = pipeline.run(inputs)
        duration_s = max(0.001, time.monotonic() - t0)
    except Exception as exc:
        duration_s = max(0.001, time.monotonic() - t0)
        _log.warning("e3_plan_failed", request_id=request.id, run_idx=run_idx, error=str(exc))
        empty_plan = SitePlan(
            plan_id=plan_id,
            site_name=site_name,
            setting_inferred=request.domain,
            languages=("en",),
            request=(RequestItem(kind="text", text=request.text),),
            inventory=(),
            feasibility=(),
            rules=(),
            privacy_mode=PrivacyMode.STANDARD,
            acceptance=AcceptanceResult(checks=(), rounds=0),
        )
        record = RequestRunRecord(
            request_id=request.id,
            run_index=run_idx,
            outcome="failed",
            is_valid_or_honest=False,
            first_pass_accepted=False,
            repair_rounds=0,
            duration_s=round(duration_s, 3),
        )
        return empty_plan, record

    not_obs = [f for f in plan.feasibility if f.verdict == "not_observable"]
    declined = [f for f in plan.feasibility if f.verdict == "declined"]
    if not_obs and not plan.rules:
        is_honest = request.expected_outcome == "not_observable"
        record = RequestRunRecord(
            request_id=request.id,
            run_index=run_idx,
            outcome="not_observable",
            is_valid_or_honest=is_honest,
            first_pass_accepted=True,
            repair_rounds=plan.acceptance.rounds,
            duration_s=round(duration_s, 3),
            not_observable_reason=not_obs[0].says,
        )
        return plan, record
    if declined and not plan.rules:
        is_honest = request.expected_outcome == "declined"
        record = RequestRunRecord(
            request_id=request.id,
            run_index=run_idx,
            outcome="declined",
            is_valid_or_honest=is_honest,
            first_pass_accepted=True,
            repair_rounds=plan.acceptance.rounds,
            duration_s=round(duration_s, 3),
            declined_reason=declined[0].reason or declined[0].says,
        )
        return plan, record
    if plan.rules:
        all_passed = plan.acceptance.all_passed
        is_valid = all_passed and (request.expected_outcome == "observable")
        record = RequestRunRecord(
            request_id=request.id,
            run_index=run_idx,
            outcome="valid_rule",
            is_valid_or_honest=is_valid,
            first_pass_accepted=(plan.acceptance.rounds == 0 and all_passed),
            repair_rounds=plan.acceptance.rounds,
            duration_s=round(duration_s, 3),
            rule_ids=[r.id for r in plan.rules],
        )
        return plan, record
    record = RequestRunRecord(
        request_id=request.id,
        run_index=run_idx,
        outcome="failed",
        is_valid_or_honest=False,
        first_pass_accepted=False,
        repair_rounds=plan.acceptance.rounds,
        duration_s=round(duration_s, 3),
    )
    return plan, record


def aggregate_e3(
    records: Sequence[RequestRunRecord],
    total_requests: int,
    *,
    simulated: bool,
    has_outside_requests: bool = False,
    any_team_written: bool = True,
    team_written_count: int = 0,
) -> E3Summary:
    """Aggregate run records into summary statistics; non-outside runs never pass the gate."""
    total_runs = len(records)
    if total_runs == 0:
        return E3Summary(
            total_requests=total_requests,
            runs_per_request=0,
            total_runs=0,
            valid_or_honest_count=0,
            valid_or_honest_rate=0.0,
            first_pass_count=0,
            first_pass_acceptance_rate=0.0,
            mean_repair_rounds=0.0,
            mean_duration_s=0.0,
            simulated=simulated,
            has_outside_requests=has_outside_requests,
            any_team_written=any_team_written,
            team_written_count=team_written_count,
            gate_passed=False,
            runs=[],
        )

    valid_honest = sum(1 for r in records if r.is_valid_or_honest)
    first_pass = sum(1 for r in records if r.first_pass_accepted)
    mean_repairs = sum(r.repair_rounds for r in records) / total_runs
    mean_duration = sum(r.duration_s for r in records) / total_runs
    valid_rate = valid_honest / total_runs
    first_pass_rate = first_pass / total_runs

    gate_unblocked = (
        not simulated
        and has_outside_requests
        and not any_team_written
        and valid_rate >= TARGET_VALID_OR_HONEST_RATE
    )

    return E3Summary(
        total_requests=total_requests,
        runs_per_request=total_runs // total_requests if total_requests else 0,
        total_runs=total_runs,
        valid_or_honest_count=valid_honest,
        valid_or_honest_rate=round(valid_rate, 4),
        first_pass_count=first_pass,
        first_pass_acceptance_rate=round(first_pass_rate, 4),
        mean_repair_rounds=round(mean_repairs, 3),
        mean_duration_s=round(mean_duration, 3),
        simulated=simulated,
        has_outside_requests=has_outside_requests,
        any_team_written=any_team_written,
        team_written_count=team_written_count,
        gate_passed=gate_unblocked,
        runs=list(records),
    )


def render_e3_markdown_table(summary: E3Summary) -> str:
    """Render the E3 result table for RESULTS.md, labelling simulated runs as such."""
    pct_valid = summary.valid_or_honest_rate * 100.0
    pct_first_pass = summary.first_pass_acceptance_rate * 100.0
    if summary.simulated:
        title = "### Evaluation E3: Held-Out Requests (SIMULATED HARNESS, not a Planner run)"
        status_str = "SIMULATED: no gate"
    elif summary.any_team_written or not summary.has_outside_requests:
        title = "### Evaluation E3: Held-Out Requests (TEAM-WRITTEN: outside authors required)"
        status_str = "BLOCKED: team-written requests"
    else:
        title = "### Evaluation E3: Held-Out Requests Generalisation"
        status_str = "PASS (Gate unblocked)" if summary.gate_passed else "FAIL"

    repair_status = (
        "—"
        if summary.simulated
        else ("PASS" if summary.mean_repair_rounds <= TARGET_MEAN_REPAIR_ROUNDS else "FAIL")
    )
    lines = [
        title,
        "",
        "| Metric | Target | Measured | Result |",
        "| :--- | :--- | :--- | :--- |",
        (
            f"| **Valid-or-Honest Rate** | $\\ge 80.0\\%$ | "
            f"**{pct_valid:.1f}%** ({summary.valid_or_honest_count}/{summary.total_runs}) | "
            f"**{status_str}** |"
        ),
        (
            f"| **First-Pass Acceptance** | — | "
            f"{pct_first_pass:.1f}% ({summary.first_pass_count}/{summary.total_runs}) | — |"
        ),
        (
            f"| **Mean Repair Rounds** | $\\le 1.0$ | "
            f"{summary.mean_repair_rounds:.2f} rounds | {repair_status} |"
        ),
    ]
    if not summary.simulated:
        lines.append(f"| **Mean Plan Generation Time** | — | {summary.mean_duration_s:.2f} s | — |")
    footnote = (
        f"*Total held-out requests: {summary.total_requests} "
        f"(run {summary.runs_per_request}x each = {summary.total_runs} total runs).*"
    )
    if summary.simulated:
        footnote = (
            f"*Simulated: `simulate_plan_for_request` stood in for the Planner on "
            f"{summary.total_requests} requests x {summary.runs_per_request} runs. "
            "These numbers are true by construction and are not evidence of generalisation.*"
        )
    elif summary.any_team_written or not summary.has_outside_requests:
        if summary.team_written_count in (summary.total_requests, 0):
            count_desc = f"all {summary.total_requests}"
        else:
            count_desc = f"{summary.team_written_count} of {summary.total_requests}"
        footnote = (
            f"*Team-written: {count_desc} requests are team-written personas. "
            "Per S-04 and W-10, gate passage requires requests written by people outside the team.*"
        )
    lines.extend(["", footnote])
    return "\n".join(lines)


def run_e3_evaluation(
    requests_path: Path,
    plans_dir: Path,
    results_path: Path,
    *,
    runs_per_request: int = DEFAULT_RUNS_PER_REQUEST,
    pipeline: PlannerPipeline | None = None,
    camera_frames: dict[str, tuple[Path, ...]] | None = None,
) -> E3Summary:
    """Run the E3 evaluation over held-out requests and write plans and a summary.

    When `pipeline` is provided, requests are evaluated through the actual PlannerPipeline
    and `simulated=False`. When `pipeline` is None, runs the simulated harness.
    """
    requests = load_heldout_requests(requests_path)
    plans_dir.mkdir(parents=True, exist_ok=True)
    results_path.parent.mkdir(parents=True, exist_ok=True)

    records: list[RequestRunRecord] = []

    for req in requests:
        for run_idx in range(runs_per_request):
            if pipeline is not None:
                plan, record = plan_for_request(req, run_idx, pipeline, camera_frames=camera_frames)
            else:
                plan, record = simulate_plan_for_request(req, run_idx)
            records.append(record)

            json_file = plans_dir / f"{plan.plan_id}.json"
            yaml_file = plans_dir / f"{plan.plan_id}.yaml"
            json_file.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
            yaml_file.write_text(plan_to_yaml(plan), encoding="utf-8")

    outside_mask = [is_outside_author(req.author) for req in requests]
    has_outside_requests = all(outside_mask) and len(requests) > 0
    any_team_written = not all(outside_mask)
    team_written_count = sum(1 for is_out in outside_mask if not is_out)

    summary = aggregate_e3(
        records,
        len(requests),
        simulated=(pipeline is None),
        has_outside_requests=has_outside_requests,
        any_team_written=any_team_written,
        team_written_count=team_written_count,
    )

    results_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    _log.info(
        "e3_evaluation_complete",
        total_requests=summary.total_requests,
        total_runs=summary.total_runs,
        valid_rate=summary.valid_or_honest_rate,
        passed=summary.gate_passed,
    )
    return summary
