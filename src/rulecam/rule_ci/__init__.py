"""Rule CI: traces and mutations (P2-08); scenes, lookalikes, injection, forecast (P2-09)."""

from rulecam.rule_ci.forecast import AlertForecastReport, compute_alert_forecast
from rulecam.rule_ci.geometry import UnsupportedRuleError
from rulecam.rule_ci.injection import (
    InjectionResult,
    composite_sign_on_image,
    run_injection_red_team,
)
from rulecam.rule_ci.lookalikes import (
    LookalikeFinding,
    LookalikeList,
    LookalikeReport,
    run_lookalike_red_team,
)
from rulecam.rule_ci.models import CIResult, MutationCase, RuleCIReport, TraceCase
from rulecam.rule_ci.mutations import generate_mutations
from rulecam.rule_ci.runner import prove_plan_hook, run_rule_ci
from rulecam.rule_ci.scenes import (
    DetectabilityMatrix,
    DetectabilityReport,
    run_detectability_scenes,
)
from rulecam.rule_ci.traces import generate_rule_traces

__all__ = [
    "AlertForecastReport",
    "CIResult",
    "DetectabilityMatrix",
    "DetectabilityReport",
    "InjectionResult",
    "LookalikeFinding",
    "LookalikeList",
    "LookalikeReport",
    "MutationCase",
    "RuleCIReport",
    "TraceCase",
    "UnsupportedRuleError",
    "composite_sign_on_image",
    "compute_alert_forecast",
    "generate_mutations",
    "generate_rule_traces",
    "prove_plan_hook",
    "run_detectability_scenes",
    "run_injection_red_team",
    "run_lookalike_red_team",
    "run_rule_ci",
]
