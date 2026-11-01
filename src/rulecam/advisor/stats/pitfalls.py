"""Checks for two classic ways a before/after comparison misleads (task P4-08b)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class StratumMeans:
    """Means and counts of one stratum in period A and period B."""

    mean_a: float
    n_a: int
    mean_b: float
    n_b: int


def simpsons_paradox(strata: Sequence[StratumMeans]) -> bool:
    """Whether the pooled difference `b - a` points the opposite way to every stratum.

    True when all strata agree on a non-zero direction and the pooled means disagree with it.
    Strata without observations in both periods are ignored.
    """
    usable = [s for s in strata if s.n_a > 0 and s.n_b > 0]
    if not usable:
        return False
    signs = {(s.mean_b > s.mean_a) - (s.mean_b < s.mean_a) for s in usable}
    if len(signs) != 1 or 0 in signs:
        return False
    total_a = sum(s.n_a for s in usable)
    total_b = sum(s.n_b for s in usable)
    pooled_a = sum(s.mean_a * s.n_a for s in usable) / total_a
    pooled_b = sum(s.mean_b * s.n_b for s in usable) / total_b
    pooled_sign = (pooled_b > pooled_a) - (pooled_b < pooled_a)
    return pooled_sign == -signs.pop()


@dataclass(frozen=True)
class RegressionToMean:
    """How much of an observed change is expected from regression to the mean alone."""

    observed_change: float
    expected_change: float
    adjusted_change: float


def regression_to_mean(
    selected_first_mean: float,
    selected_second_mean: float,
    population_mean: float,
    correlation: float,
) -> RegressionToMean:
    """Separate regression to the mean from a change in a group picked for being extreme.

    A group selected because it scored high (or low) in period 1 is expected to move toward the
    population mean in period 2 by `(1 - correlation)` of its distance, with no intervention.

    Args:
        selected_first_mean: The group's mean in the selection period.
        selected_second_mean: The group's mean afterwards.
        population_mean: The mean of the whole population.
        correlation: Correlation between the two periods across units, in [-1, 1].

    Returns:
        The observed change, the change regression alone predicts, and the difference.

    Raises:
        ValueError: If `correlation` is outside [-1, 1].
    """
    if not -1.0 <= correlation <= 1.0:
        raise ValueError("correlation must be in [-1, 1]")
    expected_second = population_mean + correlation * (selected_first_mean - population_mean)
    observed = selected_second_mean - selected_first_mean
    expected = expected_second - selected_first_mean
    return RegressionToMean(observed, expected, observed - expected)
