"""Rule CI: traces and mutations (P2-08); scenes, lookalikes, injection, forecast (P2-09)."""

from rulecam.rule_ci.forecast import AlertBudget, AlertForecast, forecast_alerts
from rulecam.rule_ci.geometry import UnsupportedRuleError
from rulecam.rule_ci.injection import InjectionReport, run_injection_red_team
from rulecam.rule_ci.lookalikes import (
    LookalikeItem,
    LookalikeReport,
    append_regression,
    run_lookalike_red_team,
)
from rulecam.rule_ci.models import CIResult, MutationCase, RuleCIReport, TraceCase
from rulecam.rule_ci.mutations import generate_mutations
from rulecam.rule_ci.red_team import RedTeam, RedTeamReport
from rulecam.rule_ci.runner import prove_plan_hook, run_rule_ci
from rulecam.rule_ci.scenes import DetectabilityReport, run_detectability_scenes
from rulecam.rule_ci.traces import generate_rule_traces

__all__ = [
    "AlertBudget",
    "AlertForecast",
    "CIResult",
    "DetectabilityReport",
    "InjectionReport",
    "LookalikeItem",
    "LookalikeReport",
    "MutationCase",
    "RedTeam",
    "RedTeamReport",
    "RuleCIReport",
    "TraceCase",
    "UnsupportedRuleError",
    "append_regression",
    "forecast_alerts",
    "generate_mutations",
    "generate_rule_traces",
    "prove_plan_hook",
    "run_detectability_scenes",
    "run_injection_red_team",
    "run_lookalike_red_team",
    "run_rule_ci",
]
