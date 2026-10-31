"""Rule CI runner and the planner's step 10 hook (task P2-08, MP §3.4).

`run_rule_ci` evaluates a rule with the real rules engine (`rulecam.rules.evaluate`) on its
boundary traces, then evaluates every mutant on the same traces. A mutant is killed when at least
one trace's outcome differs from the unmutated rule's; survivors are listed in the report. The
report is published on `ci.result` as a `CIResult` (`{rule_id, report}` plus `ci_green`).

`prove_plan_hook` runs Rule CI for every rule of a plan and records `ci_green` and `nonvacuous`
for each rule in the plan's acceptance trace (step 8 leaves them out until Rule CI has run).
Given a red team (P2-09, `rule_ci.red_team.RedTeam`), it also runs part 2 for every rule and
attaches the result to the rule's report, so it reaches `ci.result` and `ci_green`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Final

from rulecam.bus import Bus, Topic
from rulecam.contracts.core import Calibration
from rulecam.contracts.plan import AcceptanceCheck, SitePlan
from rulecam.contracts.rule_ir import RuleIR
from rulecam.log import get_logger
from rulecam.planner.acceptance import RULE_CI_CHECKS
from rulecam.rule_ci.generate import generate_rule_traces
from rulecam.rule_ci.geometry import UnsupportedRuleError
from rulecam.rule_ci.models import CIResult, MutationCase, RuleCIReport, TraceCase
from rulecam.rule_ci.mutations import generate_mutations
from rulecam.rule_ci.red_team import RedTeamReport
from rulecam.rules import RuleEvaluationError, evaluate

_log = get_logger(__name__)

Outcome = bool | str
"""Whether the rule fired on a trace, or the evaluation error's text."""


def fires(rule: RuleIR, trace: TraceCase, *, operator_exclusion: bool = True) -> Outcome:
    """Run the rules engine on one trace.

    Zones are not passed in, so the engine resolves them from the rule itself (a mutant's
    widened zones included) and builds buffer zones around the trace's anchor tracks.

    Args:
        rule: The rule (or a mutant).
        trace: The trace.
        operator_exclusion: The engine's operator exclusion (on, as in production; the
            `drop_operator_exclusion` mutant turns it off).

    Returns:
        True if at least one candidate was emitted, False if none, or the error text if the
        engine rejected the rule or the window.
    """
    try:
        events = evaluate(
            rule,
            trace.tracks,
            calibration=trace.calibration,
            zones={},
            t0=trace.t0,
            t1=trace.t1,
            operator_exclusion=operator_exclusion,
        )
    except RuleEvaluationError as exc:
        return f"error: {exc}"
    return bool(events)


NONVACUOUS_KINDS: Final = frozenset({"boundary", "zone_edge"})
"""Pairs that must be straddled: thresholds and zone edges (gap and window pairs are tolerances)."""


def nonvacuous(traces: Sequence[TraceCase], outcomes: Mapping[str, Outcome]) -> bool:
    """Whether the rule fires on the positive and not on the negative trace of every pair.

    Every threshold and zone-edge pair must have a positive member the rule fires on and a
    negative member it stays quiet on. A rule that never fires, always fires, or has a threshold
    so degenerate that no positive member exists (a 0 s dwell) is vacuous.

    Args:
        traces: The traces.
        outcomes: The unmutated rule's outcome per trace name.

    Returns:
        True when there is at least one such pair and all of them are straddled.
    """
    pairs: dict[str, tuple[bool, bool]] = {}
    for trace in traces:
        if trace.pair is None or trace.kind not in NONVACUOUS_KINDS:
            continue
        fired_on_positive, quiet_on_negative = pairs.get(trace.pair, (False, False))
        observed = outcomes[trace.name]
        if trace.expected_violation:
            fired_on_positive |= observed is True
        else:
            quiet_on_negative |= observed is False
        pairs[trace.pair] = (fired_on_positive, quiet_on_negative)
    return bool(pairs) and all(positive and negative for positive, negative in pairs.values())


def _trace_failures(traces: Sequence[TraceCase], outcomes: Mapping[str, Outcome]) -> list[str]:
    failures = []
    for trace in traces:
        observed = outcomes[trace.name]
        if observed != trace.expected_violation:
            failures.append(
                f"trace {trace.name}: expected violation={trace.expected_violation}, "
                f"got {observed} ({trace.description})"
            )
    return failures


def killed(
    mutant: MutationCase, traces: Sequence[TraceCase], baseline: Mapping[str, Outcome]
) -> bool:
    """Whether some trace's outcome under the mutant differs from the unmutated rule's."""
    return any(
        fires(mutant.rule, trace, operator_exclusion=mutant.operator_exclusion)
        != baseline[trace.name]
        for trace in traces
    )


def run_rule_ci(
    rule: RuleIR,
    *,
    bus: Bus | None = None,
    traces: Sequence[TraceCase] | None = None,
    mutants: Sequence[MutationCase] | None = None,
    justification: str | None = None,
    red_team: RedTeamReport | None = None,
    calibrations: Mapping[str, Calibration] | None = None,
) -> RuleCIReport:
    """Run boundary traces and mutation tests for one rule and publish the report.

    Args:
        rule: The rule.
        bus: Where to publish `ci.result`, if anywhere.
        traces: Traces to use instead of `generate_rule_traces(rule)`.
        mutants: Mutants to use instead of `generate_mutations(rule)`.
        justification: Recorded reason to accept a kill ratio below 90%.
        red_team: Part 2's result for the rule (P2-09); its problems join `failures`.
        calibrations: Real ground calibrations by camera, which buffer-zone traces are laid out
            through (`generate_rule_traces`).

    Returns:
        The report. A rule Rule CI cannot generate traces for gets an empty, failing report.
    """
    failures: list[str] = []
    try:
        cases = (
            list(traces)
            if traces is not None
            else generate_rule_traces(rule, calibrations=calibrations)
        )
    except UnsupportedRuleError as exc:
        cases, failures = [], [f"no boundary traces: {exc}"]
    outcomes = {trace.name: fires(rule, trace) for trace in cases}
    failures += _trace_failures(cases, outcomes)
    variants = list(mutants) if mutants is not None else generate_mutations(rule)
    survivors = [m for m in variants if cases and not killed(m, cases, outcomes)]
    unrun = len(variants) if not cases else 0
    failures += [f"mutant {m.name} survived ({m.description})" for m in survivors]
    if unrun:
        failures.append(f"{unrun} mutants not run: no traces")
    if red_team is not None:
        failures += red_team.problems()
    report = RuleCIReport(
        rule_id=rule.id,
        traces_passed=sum(outcomes[t.name] == t.expected_violation for t in cases),
        traces_total=len(cases),
        mutants_killed=len(variants) - len(survivors) - unrun,
        mutants_total=len(variants),
        failures=tuple(failures),
        nonvacuous=nonvacuous(cases, outcomes),
        justification=justification,
        red_team=red_team,
    )
    if bus is not None:
        bus.publish(Topic.CI_RESULT, CIResult.of(report))
    _log.info("rule_ci_done", rule_id=rule.id, summary=report.summary(), ci_green=report.ci_green)
    return report


def acceptance_checks(report: RuleCIReport) -> tuple[AcceptanceCheck, AcceptanceCheck]:
    """The `ci_green` and `nonvacuous` acceptance checks for one rule's report."""
    detail = report.summary()
    if report.failures:
        detail += "; " + "; ".join(report.failures)
    return (
        AcceptanceCheck(
            item=report.rule_id, check="ci_green", passed=report.ci_green, detail=detail
        ),
        AcceptanceCheck(
            item=report.rule_id,
            check="nonvacuous",
            passed=report.nonvacuous,
            detail="fires on its positive boundary trace and not on its negative one"
            if report.nonvacuous
            else "no boundary pair where it fires on the positive and not the negative trace",
        ),
    )


def with_rule_ci(plan: SitePlan, reports: Sequence[RuleCIReport]) -> SitePlan:
    """The plan with its acceptance trace's Rule CI checks replaced by these reports'."""
    kept = tuple(c for c in plan.acceptance.checks if c.check not in RULE_CI_CHECKS)
    added = tuple(check for report in reports for check in acceptance_checks(report))
    acceptance = plan.acceptance.model_copy(update={"checks": kept + added})
    return plan.model_copy(update={"acceptance": acceptance})


def prove_plan_hook(
    bus: Bus | None = None,
    *,
    justifications: Mapping[str, str] | None = None,
    red_team: Callable[[RuleIR], RedTeamReport] | None = None,
    calibrations: Callable[[SitePlan], Mapping[str, Calibration]] | None = None,
) -> Callable[[SitePlan], SitePlan]:
    """Build the pipeline's step 10 hook: Rule CI for every rule, recorded in the acceptance trace.

    Args:
        bus: Where to publish each rule's `ci.result`.
        justifications: Recorded reasons, by rule id, to accept a kill ratio below 90%.
        red_team: Part 2 per rule (`RedTeam`); without it only part 1 runs.
        calibrations: The plan run's ground calibrations by camera (`PlanStore.calibrations`);
            without them a buffer rule's traces use the synthetic ground plane its
            `calibration` names.

    Returns:
        A `SitePlan -> SitePlan` hook.
    """
    reasons = dict(justifications or {})

    def hook(plan: SitePlan) -> SitePlan:
        cameras = calibrations(plan) if calibrations is not None else {}
        reports = [
            run_rule_ci(
                rule,
                bus=bus,
                justification=reasons.get(rule.id),
                red_team=red_team(rule) if red_team is not None else None,
                calibrations=cameras,
            )
            for rule in plan.rules
        ]
        return with_rule_ci(plan, reports)

    return hook
