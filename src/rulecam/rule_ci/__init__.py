"""Rule CI boundary-trace fuzzing and mutation testing package (task P2-08)."""

from rulecam.rule_ci.models import MutationCase, RuleCIReport, TraceCase
from rulecam.rule_ci.mutations import generate_mutations
from rulecam.rule_ci.runner import prove_plan_hook, run_rule_ci
from rulecam.rule_ci.traces import generate_rule_traces, is_rule_vacuous

__all__ = [
    "MutationCase",
    "RuleCIReport",
    "TraceCase",
    "generate_mutations",
    "generate_rule_traces",
    "is_rule_vacuous",
    "prove_plan_hook",
    "run_rule_ci",
]
