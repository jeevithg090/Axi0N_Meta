"""Pre-registered switchback plans and their read-out (task P4-08b, SA §4 steps 15-16).

The plan is hashed before the change starts, so the method cannot be adjusted after seeing the
data. Switchback is the only design built here: before/after designs are confounded by day-level
effects, so they are left to a fallback with weaker wording.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Literal

from rulecam.advisor.stats.switchback import (
    min_detectable_effect,
    sign_flip_ci,
    sign_flip_p,
    smallest_possible_p,
)
from rulecam.contracts.advisor import MeasurementPlan, Verdict

Direction = Literal["lower_is_better", "higher_is_better"]
ALPHA = 0.05


def _hash(fields: dict[str, object]) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def decision_rule(primary_metric: str, direction: Direction, mde: float) -> str:
    """State the verdict rule in words, so it is hashed with the plan."""
    better = "below" if direction == "lower_is_better" else "above"
    worse = "above" if direction == "lower_is_better" else "below"
    return (
        f"On-minus-off difference of {primary_metric} per pair of blocks, exact sign-flip test, "
        f"{(1 - ALPHA):.0%} interval. worked: the whole interval is {better} 0. worse: the whole "
        f"interval is {worse} 0. no_effect: the interval lies within +/-{mde:g}. Otherwise, or "
        "before all pairs are done: not_enough_data."
    )


def build_measurement_plan(
    *,
    recommendation_id: str,
    primary_metric: str,
    guard_metrics: Sequence[str],
    block_minutes: int,
    n_pairs: int,
    block_difference_sigma: float,
    direction: Direction = "lower_is_better",
) -> MeasurementPlan:
    """Build a switchback plan and its preregistration hash.

    Args:
        recommendation_id: The accepted recommendation.
        primary_metric: The metric the verdict is about.
        guard_metrics: Metrics reported alongside, such as near-misses per forklift-hour.
        block_minutes: Length of each on or off block, in set minutes.
        n_pairs: Pairs of on and off blocks. Each block starts and ends with an empty queue.
        block_difference_sigma: Expected spread of the per-pair differences, from earlier data.
        direction: Whether a lower or a higher value of the primary metric is better.

    Returns:
        The plan, with its minimum detectable effect and smallest possible p-value stated.

    Raises:
        ValueError: If `n_pairs` are too few to reach significance, or `block_minutes` is not
            positive.
    """
    if block_minutes < 1:
        raise ValueError("block_minutes must be at least 1")
    mde = min_detectable_effect(block_difference_sigma, n_pairs, ALPHA)
    fields: dict[str, object] = {
        "id": f"mp-{recommendation_id}",
        "recommendation_id": recommendation_id,
        "primary_metric": primary_metric,
        "guard_metrics": list(guard_metrics),
        "design": "switchback",
        "block_minutes": block_minutes,
        "n_pairs": n_pairs,
        "min_detectable_effect": mde,
        "smallest_possible_p": smallest_possible_p(n_pairs),
        "decision_rule": decision_rule(primary_metric, direction, mde),
    }
    return MeasurementPlan.model_validate({**fields, "preregistration_hash": _hash(fields)})


def plan_hash(plan: MeasurementPlan) -> str:
    """Recompute a plan's hash from its fields, to check it was not altered."""
    fields = plan.model_dump(exclude={"preregistration_hash"})
    fields["guard_metrics"] = list(plan.guard_metrics)
    return _hash(fields)


def read_out(
    plan: MeasurementPlan, differences: Sequence[float], direction: Direction = "lower_is_better"
) -> Verdict:
    """Apply the plan's decision rule to the per-pair differences collected so far.

    Args:
        plan: The pre-registered plan.
        differences: On-minus-off difference of the primary metric for each finished pair.
        direction: Whether a lower or a higher value of the primary metric is better.

    Returns:
        The verdict. It is `not_enough_data` until every planned pair is done. While too few
        pairs exist to form any interval, `ci` is the range of the observed differences instead
        (an interval can't be infinite in the evidence log), and it must not be read as one.

    Raises:
        ValueError: If there are no differences, or more than the plan has pairs.
    """
    if not differences or len(differences) > plan.n_pairs:
        raise ValueError(f"need between 1 and {plan.n_pairs} differences")
    low, high = sign_flip_ci(differences, ALPHA)
    if math.isinf(low):
        low, high = min(differences), max(differences)
    p_value = sign_flip_p(differences)
    effect = sum(differences) / len(differences)
    label: Literal["worked", "no_effect", "worse", "not_enough_data"] = "not_enough_data"
    if len(differences) == plan.n_pairs:
        good_side = high < 0 if direction == "lower_is_better" else low > 0
        bad_side = low > 0 if direction == "lower_is_better" else high < 0
        mde = plan.min_detectable_effect
        if good_side:
            label = "worked"
        elif bad_side:
            label = "worse"
        elif -mde <= low and high <= mde:
            label = "no_effect"
    return Verdict(
        plan_id=plan.id,
        label=label,
        effect=effect,
        ci=(low, high),
        p_value=p_value,
        pairs_done=len(differences),
    )
