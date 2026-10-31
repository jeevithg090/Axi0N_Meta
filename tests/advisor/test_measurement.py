from __future__ import annotations

import pytest

from rulecam.advisor.measurement import build_measurement_plan, plan_hash, read_out
from rulecam.contracts import MeasurementPlan, Verdict


def plan(
    n_pairs: int = 6, sigma: float = 3.0, direction: str = "lower_is_better"
) -> MeasurementPlan:
    return build_measurement_plan(
        recommendation_id="rec-0007",
        primary_metric="wait_for_forklift",
        guard_metrics=("near_miss_per_forklift_hour",),
        block_minutes=10,
        n_pairs=n_pairs,
        block_difference_sigma=sigma,
        direction=direction,  # type: ignore[arg-type]
    )


def test_plan_states_its_limits_before_the_change() -> None:
    p = plan()
    assert p.design == "switchback"
    assert p.smallest_possible_p == 0.03125
    assert p.min_detectable_effect == pytest.approx(3.0 * 1.7927, rel=1e-3)
    assert p.id == "mp-rec-0007"
    assert "worked" in p.decision_rule
    assert "wait_for_forklift" in p.decision_rule


def test_hash_detects_any_change_to_the_plan() -> None:
    p = plan()
    assert p.preregistration_hash == plan_hash(p)
    altered = p.model_copy(update={"n_pairs": 7})
    assert plan_hash(altered) != p.preregistration_hash
    assert plan() == p  # deterministic


def test_underpowered_plans_are_refused() -> None:
    with pytest.raises(ValueError, match="never reach"):
        plan(n_pairs=3)
    with pytest.raises(ValueError, match="block_minutes"):
        build_measurement_plan(
            recommendation_id="r", primary_metric="m", guard_metrics=(), block_minutes=0,
            n_pairs=6, block_difference_sigma=1.0,
        )  # fmt: skip


def test_a_clear_improvement_is_worked() -> None:
    verdict = read_out(plan(), [-9.0, -8.0, -10.0, -7.0, -11.0, -9.0])
    assert verdict.label == "worked"
    assert verdict.ci[1] < 0
    assert verdict.pairs_done == 6
    assert verdict.p_value == 0.03125


def test_a_clear_deterioration_is_worse() -> None:
    assert read_out(plan(), [8.0, 9.0, 7.0, 10.0, 9.5, 8.5]).label == "worse"


def test_direction_flips_the_good_side() -> None:
    p = plan(direction="higher_is_better")
    diffs = [8.0, 9.0, 7.0, 10.0, 9.5, 8.5]
    assert read_out(p, diffs, "higher_is_better").label == "worked"
    assert read_out(p, [-d for d in diffs], "higher_is_better").label == "worse"


def test_small_differences_inside_the_detectable_effect_are_no_effect() -> None:
    p = plan(sigma=5.0)
    verdict = read_out(p, [0.1, -0.1, 0.2, -0.2, 0.05, -0.05])
    assert verdict.label == "no_effect"


def test_a_noisy_result_is_not_enough_data() -> None:
    p = plan(sigma=0.5)
    assert read_out(p, [3.0, -4.0, 5.0, -2.0, 4.0, -3.0]).label == "not_enough_data"


def test_interim_results_are_labelled_and_round_trip_through_json() -> None:
    verdict = read_out(plan(), [-9.0, -8.0, -10.0])
    assert verdict.label == "not_enough_data"
    assert verdict.pairs_done == 3
    assert verdict.ci == (-10.0, -8.0)  # the observed range: too few pairs for an interval
    assert Verdict.model_validate_json(verdict.model_dump_json()) == verdict


def test_read_out_rejects_bad_sizes() -> None:
    with pytest.raises(ValueError, match="between 1 and 6"):
        read_out(plan(), [])
    with pytest.raises(ValueError, match="between 1 and 6"):
        read_out(plan(), [1.0] * 7)
