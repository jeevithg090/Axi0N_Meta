from __future__ import annotations

import math

import pytest

from rulecam.advisor.stats import (
    min_detectable_effect,
    minimum_pairs,
    sign_flip_ci,
    sign_flip_p,
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


def test_min_detectable_effect_matches_the_normal_formula() -> None:
    assert min_detectable_effect(3.0, 6) == pytest.approx(2.80157 * 3.0 / math.sqrt(6), rel=1e-4)
    assert min_detectable_effect(0.0, 6) == 0.0


def test_min_detectable_effect_refuses_plans_that_cannot_reach_significance() -> None:
    with pytest.raises(ValueError, match="never reach") as error:
        min_detectable_effect(3.0, 3)
    assert "at least 6 pairs" in str(error.value)
    with pytest.raises(ValueError, match="sigma"):
        min_detectable_effect(-1.0, 6)
