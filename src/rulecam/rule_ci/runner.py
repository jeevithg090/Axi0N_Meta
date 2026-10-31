"""Rule CI test execution runner (task P2-08, MP §3.4).

Coordinates boundary-trace fuzzing and mutation testing for RuleIR rules:
1. Validates non-vacuity (threshold > 0, valid bounds).
2. Runs synthetic Track sets and verifies expected violation outcomes.
3. Fuzzes with mutation operators (scaling, flipping comparisons, semantics direction).
4. Tracks killed mutants (outcome differs from baseline).
5. Publishes results to `Topic.CI_RESULT` and returns `RuleCIReport`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from rulecam.bus import Bus, Topic
from rulecam.contracts.core import Zone
from rulecam.contracts.plan import SitePlan
from rulecam.contracts.rule_ir import RuleIR
from rulecam.log import get_logger
from rulecam.rule_ci.models import MutationCase, RuleCIReport, TraceCase
from rulecam.rule_ci.mutations import generate_mutations
from rulecam.rule_ci.traces import generate_rule_traces, is_rule_vacuous
from rulecam.rules.engine import evaluate

_log = get_logger(__name__)


def _evaluate_trace(rule: RuleIR, trace: TraceCase) -> bool:
    """Evaluate a rule on a synthetic trace, returning True if violation detected."""
    zones = dict(trace.zones)
    for zid, zspec in rule.zones.items():
        if zspec.kind == "polygon" and zspec.polygon_px:
            zones[zid] = Zone(
                zone_id=zid,
                camera_id=zspec.camera_id,
                kind="polygon",
                polygon_px=zspec.polygon_px,
            )
    events = evaluate(
        rule,
        trace.tracks,
        calibration=trace.calibration,
        zones=zones,
        t0=trace.t0,
        t1=trace.t1,
    )
    return len(events) > 0


def _run_traces(
    rule: RuleIR, trace_cases: Sequence[TraceCase]
) -> tuple[int, dict[str, bool], list[str]]:
    """Run traces against rule and record pass count and baseline outcomes."""
    traces_passed = 0
    failures: list[str] = []
    baseline_outcomes: dict[str, bool] = {}

    for trace in trace_cases:
        try:
            observed_violation = _evaluate_trace(rule, trace)
            baseline_outcomes[trace.name] = observed_violation
            if observed_violation == trace.expected_violation:
                traces_passed += 1
            else:
                failures.append(
                    f"Trace '{trace.name}' failed: expected violation={trace.expected_violation}, "
                    f"observed={observed_violation}"
                )
        except Exception as exc:
            failures.append(f"Trace '{trace.name}' raised {type(exc).__name__}: {exc}")
            baseline_outcomes[trace.name] = False

    return traces_passed, baseline_outcomes, failures


def _run_mutations(
    mutant_cases: Sequence[MutationCase],
    trace_cases: Sequence[TraceCase],
    baseline_outcomes: dict[str, bool],
) -> tuple[int, list[str]]:
    """Evaluate mutants across traces to determine killed vs surviving count."""
    mutants_killed = 0
    failures: list[str] = []

    for mutant in mutant_cases:
        killed = False
        for trace in trace_cases:
            try:
                mutant_violation = _evaluate_trace(mutant.mutated_rule, trace)
                orig_violation = baseline_outcomes.get(trace.name, False)
                if mutant_violation != orig_violation:
                    killed = True
                    break
            except Exception:
                killed = True
                break

        if killed:
            mutants_killed += 1
        else:
            failures.append(
                f"Mutant '{mutant.name}' ({mutant.operator}) survived all traces without change"
            )

    return mutants_killed, failures


def run_rule_ci(
    rule: RuleIR,
    *,
    bus: Bus | None = None,
    traces: Sequence[TraceCase] | None = None,
    mutants: Sequence[MutationCase] | None = None,
    justification: str | None = None,
) -> RuleCIReport:
    """Run boundary traces and mutation tests for a RuleIR.

    Args:
        rule: The RuleIR to test.
        bus: Event bus to publish `Topic.CI_RESULT` to.
        traces: Optional pre-generated traces (generated automatically if omitted).
        mutants: Optional pre-generated mutants (generated automatically if omitted).
        justification: Optional justification if mutant kill rate is below 90%.

    Returns:
        RuleCIReport containing results and `ci_green` status.
    """
    vacuous = is_rule_vacuous(rule)
    nonvacuous = not vacuous

    trace_cases = list(traces) if traces is not None else generate_rule_traces(rule)
    mutant_cases = list(mutants) if mutants is not None else generate_mutations(rule)

    traces_passed, baseline_outcomes, trace_failures = _run_traces(rule, trace_cases)
    mutants_killed, mutant_failures = _run_mutations(mutant_cases, trace_cases, baseline_outcomes)

    failures: list[str] = []
    if vacuous:
        failures.append(f"Rule {rule.id} has vacuous or zero threshold: nonvacuous=False")
    failures.extend(trace_failures)
    failures.extend(mutant_failures)

    report = RuleCIReport(
        rule_id=rule.id,
        traces_passed=traces_passed,
        traces_total=len(trace_cases),
        mutants_killed=mutants_killed,
        mutants_total=len(mutant_cases),
        failures=failures,
        nonvacuous=nonvacuous,
        justification=justification,
    )

    if bus is not None:
        bus.publish(
            Topic.CI_RESULT,
            {"rule_id": rule.id, "report": report.model_dump()},
        )

    _log.info(
        "rule_ci_completed",
        rule_id=rule.id,
        traces=f"{traces_passed}/{len(trace_cases)}",
        mutants=f"{mutants_killed}/{len(mutant_cases)}",
        ci_green=report.ci_green,
    )

    return report


def prove_plan_hook(
    bus: Bus | None = None,
) -> Callable[[SitePlan], SitePlan]:
    """Return a PlanHook for step 10 ('prove') that executes Rule CI for all rules."""

    def hook(plan: SitePlan) -> SitePlan:
        for rule in plan.rules:
            run_rule_ci(rule, bus=bus)
        return plan

    return hook
