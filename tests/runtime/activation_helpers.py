"""Builders for plan-activation tests: site plans with zoned rules and acceptance traces."""

from __future__ import annotations

from collections.abc import Sequence

from rulecam.contracts import (
    AcceptanceCheck,
    AcceptanceResult,
    PrivacyMode,
    RequestItem,
    RuleIR,
    SitePlan,
)
from tests.rules.helpers import make_rule

SINK_POLYGON = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]
PASSING = (
    "zone_in_view",
    "detect_lb>=0.8",
    "calibration_err<=tol",
    "privacy_consistent",
    "no_private_spaces",
    "citation_text_match",
    "ci_green",
    "nonvacuous",
)


def sink_rule(
    rule_id: str = "in-sink", polygon: Sequence[Sequence[float]] = SINK_POLYGON
) -> RuleIR:
    """A rule that fires when a person stands in the sink polygon on cam1."""
    return make_rule(
        {"op": "inside", "object": "person", "zone": "sink_zone"},
        rule_id=rule_id,
        zones={"sink_zone": {"kind": "polygon", "camera_id": "cam1", "polygon_px": polygon}},
    )


def buffer_rule(rule_id: str = "near-fryer") -> RuleIR:
    """A rule with a metric buffer zone around a fryer on cam2."""
    return make_rule(
        {"op": "inside", "object": "person", "zone": "fryer_buffer"},
        concepts={"person": "person", "fryer": "fryer"},
        rule_id=rule_id,
        zones={
            "fryer_buffer": {
                "kind": "buffer",
                "camera_id": "cam2",
                "buffer_of": "fryer",
                "buffer_m": 1.5,
            }
        },
    )


def checks_for(
    rule_id: str, *, failing: Sequence[str] = (), omit: Sequence[str] = ()
) -> list[AcceptanceCheck]:
    """The full check set on `rule_id`, with `failing` checks failed and `omit` checks absent."""
    return [
        AcceptanceCheck(
            item=rule_id,
            check=name,
            passed=name not in failing,
            detail=f"{name} detail" if name in failing else "ok",
        )
        for name in PASSING
        if name not in omit
    ]


def zoned_plan(
    rules: Sequence[RuleIR],
    checks: Sequence[AcceptanceCheck],
    *,
    plan_id: str = "main-st",
    version: int = 1,
) -> SitePlan:
    """A site plan holding `rules` and the acceptance trace `checks`."""
    return SitePlan(
        plan_id=plan_id,
        version=version,
        site_name="Main St kitchen",
        setting_inferred="commercial_kitchen",
        languages=("en",),
        request=(RequestItem(kind="text", text="keep people out of the sink"),),
        inventory=(),
        feasibility=(),
        rules=tuple(rules),
        privacy_mode=PrivacyMode.ANONYMOUS,
        acceptance=AcceptanceResult(checks=tuple(checks), rounds=0),
    )
