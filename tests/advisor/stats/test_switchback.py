from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from rulecam.advisor.stats import (
    min_detectable_effect,
    minimum_pairs,
    sign_flip_ci,
    sign_flip_p,
    sign_flip_power,
    smallest_possible_p,
)

NEGATIVE = [-9.0, -8.0, -10.0, -7.0, -11.0, -9.0]


def test_smallest_possible_p_is_two_over_two_to_the_k() -> None:
    assert smallest_possible_p(3) == 0.25
    assert smallest_possible_p(6) == 0.03125
    assert smallest_possible_p(1) == 1.0
    with pytest.raises(ValueError, match="pairs"):
        smallest_possible_p(0)


def test_three_pairs_can_never_reach_significance_and_six_can() -> None:
    assert minimum_pairs(0.05) == 6
    assert sign_flip_p([-5.0, -4.0, -6.0]) == 0.25
    assert sign_flip_p([-5.0, -4.0, -6.0, -5.5, -4.5, -6.5]) == 0.03125


def test_sign_flip_p_extremes() -> None:
    assert sign_flip_p([1.0, -1.0]) == 1.0
    assert sign_flip_p([2.0] * 6, null=2.0) == 1.0
    assert sign_flip_p([2.0] * 6, null=0.0) == 0.03125


def test_sign_flip_p_rejects_bad_sizes() -> None:
    with pytest.raises(ValueError, match="pairs"):
        sign_flip_p([])
    with pytest.raises(ValueError, match="pairs"):
        sign_flip_p([1.0] * 21)


def test_interval_excludes_zero_for_a_clear_effect_and_contains_the_mean() -> None:
    low, high = sign_flip_ci(NEGATIVE)
    assert low < sum(NEGATIVE) / 6 < high < 0


def test_interval_edges_are_where_the_test_stops_rejecting() -> None:
    low, high = sign_flip_ci(NEGATIVE)
    assert sign_flip_p(NEGATIVE, low + 1e-6) > 0.05
    assert sign_flip_p(NEGATIVE, low - 1e-6) <= 0.05
    assert sign_flip_p(NEGATIVE, high - 1e-6) > 0.05
    assert sign_flip_p(NEGATIVE, high + 1e-6) <= 0.05


def test_interval_is_unbounded_when_too_few_pairs_exist() -> None:
    assert sign_flip_ci([-5.0, -4.0, -6.0]) == (-math.inf, math.inf)


def test_interval_for_noise_around_zero_contains_zero() -> None:
    low, high = sign_flip_ci([1.0, -1.2, 0.5, -0.4, 0.8, -0.9])
    assert low < 0 < high


def test_interval_rejects_bad_sizes() -> None:
    with pytest.raises(ValueError, match="pairs"):
        sign_flip_ci([])


def test_six_pairs_need_about_one_point_eight_sigma_not_the_normal_approximation() -> None:
    expected = NormalDist().inv_cdf(0.8 ** (1 / 6))  # P(all six share a sign) = 0.8
    assert min_detectable_effect(1.0, 6) == pytest.approx(expected, abs=1e-6)
    assert min_detectable_effect(3.0, 6) == pytest.approx(3.0 * expected, abs=1e-5)
    assert min_detectable_effect(1.0, 6) > 2.80157 / math.sqrt(6)  # the old, too-small figure


def test_the_normal_approximation_gave_only_about_44_percent_power_at_six_pairs() -> None:
    old = 2.80157 / math.sqrt(6)
    assert sign_flip_power(old, 1.0, 6) == pytest.approx(0.445, abs=0.005)


@pytest.mark.parametrize("pairs", [6, 7, 8, 10])
def test_the_stated_effect_really_has_the_wanted_power(pairs: int) -> None:
    effect = min_detectable_effect(1.0, pairs)
    # a different seed from the one the effect was solved with
    assert sign_flip_power(effect, 1.0, pairs, seed=12345, n_sim=8000) == pytest.approx(
        0.8, abs=0.04
    )


def test_more_pairs_detect_smaller_effects_and_zero_spread_needs_none() -> None:
    effects = [min_detectable_effect(1.0, k) for k in (6, 8, 10)]
    assert effects == sorted(effects, reverse=True)
    assert min_detectable_effect(0.0, 6) == 0.0


def test_power_at_no_effect_is_the_false_alarm_rate() -> None:
    assert sign_flip_power(0.0, 1.0, 6) == pytest.approx(2 * 0.5**6)
    assert sign_flip_power(0.0, 1.0, 8, seed=3, n_sim=8000) <= 0.06


def test_planning_limits_and_invalid_arguments() -> None:
    with pytest.raises(ValueError, match="never reach") as error:
        min_detectable_effect(3.0, 3)
    assert "at least 6 pairs" in str(error.value)
    with pytest.raises(ValueError, match="limited to 12"):
        min_detectable_effect(1.0, 13)
    with pytest.raises(ValueError, match="sigma"):
        min_detectable_effect(-1.0, 6)
    with pytest.raises(ValueError, match="sigma"):
        sign_flip_power(1.0, 0.0, 6)
    with pytest.raises(ValueError, match="power"):
        min_detectable_effect(1.0, 6, power=1.0)
