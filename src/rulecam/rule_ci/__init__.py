"""Rule CI part 1: boundary traces and mutation tests (task P2-08)."""

from rulecam.rule_ci.geometry import UnsupportedRuleError
from rulecam.rule_ci.models import CIResult, MutationCase, RuleCIReport, TraceCase
from rulecam.rule_ci.mutations import generate_mutations
from rulecam.rule_ci.runner import prove_plan_hook, run_rule_ci
from rulecam.rule_ci.traces import generate_rule_traces

__all__ = [
    "CIResult",
    "MutationCase",
    "RuleCIReport",
    "TraceCase",
    "UnsupportedRuleError",
    "generate_mutations",
    "generate_rule_traces",
    "prove_plan_hook",
    "run_rule_ci",
]
