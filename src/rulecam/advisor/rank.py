"""Ranking formula for Site Advisor recommendations (task P2-12, SA §4 step 12, P4-08).

Computes ranking score:
    rank_score = (20th_percentile_weekly_value / effort) * reversibility_factor

Classifies recommendation into:
    - 'do': high return, manageable effort, easily reversible, passes safety.
    - 'test': moderate return or high uncertainty, ideal for switchback pre-registration.
    - 'hold': low return, irreversible high effort, or safety-vetoed.
"""

from __future__ import annotations

from typing import Final, Literal

from rulecam.contracts.advisor import SafetyCheck

REVERSIBILITY_FACTORS: dict[str, float] = {
    "reschedule_arrivals": 1.0,  # purely procedural, instant revert
    "resequence": 1.0,  # priority queue rule, instant revert
    "queue_topology": 0.8,  # line / cone adjustments
    "buffer_capacity": 0.7,  # staging area re-allocation
    "move_location": 0.6,  # physical staging movement
    "add_capacity": 0.4,  # vehicle/equipment hire, high commit
}

DEFAULT_EFFORT: dict[str, float] = {
    "reschedule_arrivals": 1.5,
    "resequence": 1.0,
    "queue_topology": 2.0,
    "buffer_capacity": 2.0,
    "move_location": 3.0,
    "add_capacity": 4.5,
}


DO_THRESHOLD: Final[float] = 30.0
TEST_THRESHOLD: Final[float] = 10.0


def calculate_rank_score(
    p20_weekly_value: float,
    op: str,
    *,
    effort: float | None = None,
    reversibility: float | None = None,
    safety: SafetyCheck | None = None,
) -> tuple[float, Literal["do", "test", "hold"]]:
    """Calculate the ranking score and decision class.

    Args:
        p20_weekly_value: Pessimistic 20th-percentile weekly value / capacity freed (minutes or $).
        op: Change operation from closed vocabulary.
        effort: Relative effort score (1-5). Defaults from op.
        reversibility: Reversibility weight (0.1 - 1.0). Defaults from op.
        safety: Safety check outcome. If vetoed, immediately classes as 'hold'.

    Returns:
        `(rank_score, klass)` where klass is 'do', 'test', or 'hold'.
    """
    if safety is not None and safety.verdict == "veto":
        return 0.0, "hold"

    eff = effort if effort is not None else DEFAULT_EFFORT.get(op, 3.0)
    rev = reversibility if reversibility is not None else REVERSIBILITY_FACTORS.get(op, 0.7)

    # Pessimistic weekly score formula
    eff_clamped = max(0.5, eff)
    rev_clamped = max(0.1, min(1.0, rev))
    score = (max(0.0, p20_weekly_value) / eff_clamped) * rev_clamped

    if score >= DO_THRESHOLD:
        klass: Literal["do", "test", "hold"] = "do"
    elif score >= TEST_THRESHOLD:
        klass = "test"
    else:
        klass = "hold"

    return round(score, 2), klass
