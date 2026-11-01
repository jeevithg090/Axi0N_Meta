from __future__ import annotations

import pytest

from rulecam.advisor.stats import StratumMeans, regression_to_mean, simpsons_paradox


def test_simpsons_paradox_is_detected() -> None:
    # B is better in every stratum, but B's cases sit mostly in the low-valued stratum.
    strata = [StratumMeans(10, 10, 11, 90), StratumMeans(20, 90, 21, 10)]
    assert simpsons_paradox(strata) is True


def test_consistent_direction_is_not_a_paradox() -> None:
    assert simpsons_paradox([StratumMeans(10, 50, 12, 50), StratumMeans(20, 50, 22, 50)]) is False


def test_mixed_directions_ties_and_empty_are_not_a_paradox() -> None:
    assert simpsons_paradox([StratumMeans(10, 5, 12, 5), StratumMeans(20, 5, 18, 5)]) is False
    assert simpsons_paradox([StratumMeans(10, 5, 10, 5)]) is False
    assert simpsons_paradox([]) is False
    assert simpsons_paradox([StratumMeans(10, 0, 12, 5)]) is False


def test_regression_to_the_mean_accounts_for_part_of_the_change() -> None:
    result = regression_to_mean(10.0, 8.0, population_mean=5.0, correlation=0.5)
    assert result.observed_change == pytest.approx(-2.0)
    assert result.expected_change == pytest.approx(-2.5)
    assert result.adjusted_change == pytest.approx(0.5)


def test_perfect_correlation_expects_no_regression() -> None:
    assert regression_to_mean(10.0, 10.0, 5.0, 1.0).expected_change == 0.0


def test_regression_rejects_a_bad_correlation() -> None:
    with pytest.raises(ValueError, match="correlation"):
        regression_to_mean(1.0, 1.0, 1.0, 1.5)
