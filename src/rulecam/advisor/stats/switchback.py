"""Exact sign-flip test for switchback trials (task P4-08b).

A switchback alternates the change on and off by block. Each pair of blocks gives one difference
(on minus off). Under no effect every difference is equally likely to be positive or negative, so
the test enumerates all `2**k` sign patterns. With `k` pairs the smallest possible two-sided
p-value is `2 / 2**k`, which is why too few pairs can never reach significance.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import NormalDist

import numpy as np
from numpy.typing import NDArray

MAX_PAIRS = 20
MAX_PLAN_PAIRS = 12
MAX_EFFECT_SIGMAS = 12.0
POWER_BISECTION_STEPS = 30
GRID_POINTS = 401
BISECTION_STEPS = 40


def smallest_possible_p(pairs: int) -> float:
    """Smallest two-sided p-value `k` pairs can give: `2 / 2**k` (1.0 for a single pair)."""
    if pairs < 1:
        raise ValueError("pairs must be at least 1")
    return min(1.0, 2.0 / 2.0**pairs)


def minimum_pairs(alpha: float = 0.05) -> int:
    """Fewest pairs for which significance at `alpha` is possible at all."""
    pairs = 1
    while smallest_possible_p(pairs) > alpha:
        pairs += 1
    return pairs


def _signed_sums(magnitudes: NDArray[np.float64]) -> NDArray[np.float64]:
    """Absolute sums over every sign pattern of `magnitudes`."""
    count = magnitudes.size
    patterns = np.arange(2**count)[:, None] >> np.arange(count) & 1
    return np.abs((2 * patterns - 1) @ magnitudes)


def sign_flip_p(differences: Sequence[float], null: float = 0.0) -> float:
    """Exact two-sided sign-flip p-value for the mean difference being `null`.

    Args:
        differences: One on-minus-off difference per pair of blocks.
        null: The hypothesised mean difference.

    Returns:
        The share of sign patterns whose absolute sum is at least the observed one.

    Raises:
        ValueError: If there are no differences, or more than `MAX_PAIRS`.
    """
    centred = np.asarray(differences, dtype=np.float64) - null
    if centred.size == 0 or centred.size > MAX_PAIRS:
        raise ValueError(f"need between 1 and {MAX_PAIRS} pairs")
    observed = abs(float(centred.sum()))
    scale = max(1.0, float(np.abs(centred).sum()))
    sums = _signed_sums(np.abs(centred))
    return float(np.mean(sums >= observed - 1e-9 * scale))


def _refine(inside: float, outside: float, differences: Sequence[float], alpha: float) -> float:
    """Bisect between an accepted value and a rejected one to find the boundary."""
    for _ in range(BISECTION_STEPS):
        middle = (inside + outside) / 2.0
        if sign_flip_p(differences, middle) > alpha:
            inside = middle
        else:
            outside = middle
    return inside


def sign_flip_ci(differences: Sequence[float], alpha: float = 0.05) -> tuple[float, float]:
    """Confidence interval by inverting the sign-flip test: the values the test does not reject.

    Returns:
        `(lower, upper)`, or `(-inf, inf)` if too few pairs exist to reject any value.

    Raises:
        ValueError: If there are no differences, or more than `MAX_PAIRS`.
    """
    data = np.asarray(differences, dtype=np.float64)
    if data.size == 0 or data.size > MAX_PAIRS:
        raise ValueError(f"need between 1 and {MAX_PAIRS} pairs")
    if smallest_possible_p(int(data.size)) > alpha:
        return -math.inf, math.inf
    spread = max(float(data.max() - data.min()), float(np.abs(data).max()), 1e-9)
    grid = np.linspace(float(data.min()) - spread, float(data.max()) + spread, GRID_POINTS)
    accepted = [i for i, value in enumerate(grid) if sign_flip_p(differences, float(value)) > alpha]
    first, last = accepted[0], accepted[-1]
    lower = (
        float(grid[first])
        if first == 0
        else _refine(float(grid[first]), float(grid[first - 1]), differences, alpha)
    )
    upper = (
        float(grid[last])
        if last == GRID_POINTS - 1
        else _refine(float(grid[last]), float(grid[last + 1]), differences, alpha)
    )
    return lower, upper


def _normal_cdf(x: float) -> float:
    return NormalDist().cdf(x)


def _rejects(draws: NDArray[np.float64], alpha: float) -> NDArray[np.bool_]:
    """For each row of per-pair differences, whether the exact sign-flip test rejects zero."""
    pairs = draws.shape[1]
    patterns = (np.arange(2**pairs)[:, None] >> np.arange(pairs) & 1) * 2.0 - 1.0
    sums = np.abs(patterns @ np.abs(draws).T)  # one column per row of draws
    observed = np.abs(draws.sum(axis=1))
    scale = np.maximum(1.0, np.abs(draws).sum(axis=1))
    p_values = np.mean(sums >= (observed - 1e-9 * scale)[None, :], axis=0)
    return np.asarray(p_values <= alpha, dtype=np.bool_)


def sign_flip_power(
    effect: float,
    sigma: float,
    pairs: int,
    alpha: float = 0.05,
    *,
    seed: int = 0,
    n_sim: int = 4000,
) -> float:
    """Chance the exact sign-flip test rejects when the true mean difference is `effect`.

    Differences are drawn from a normal distribution with mean `effect` and spread `sigma`.
    With the fewest pairs that can reach significance, only a unanimous sign rejects, and the
    answer is exact: `Phi(effect/sigma)**k + Phi(-effect/sigma)**k`. Otherwise it is estimated by
    simulation.

    Raises:
        ValueError: If `sigma` is not positive, or `pairs` is outside what can be planned.
    """
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    _check_plannable(pairs, alpha)
    ratio = effect / sigma
    if pairs == minimum_pairs(alpha):
        return _normal_cdf(ratio) ** pairs + _normal_cdf(-ratio) ** pairs
    draws = np.random.default_rng(seed).standard_normal((n_sim, pairs)) + ratio
    return float(np.mean(_rejects(draws, alpha)))


def _check_plannable(pairs: int, alpha: float) -> None:
    if smallest_possible_p(pairs) > alpha:
        needed = minimum_pairs(alpha)
        raise ValueError(
            f"{pairs} pairs can never reach p <= {alpha}: the smallest possible p is "
            f"{smallest_possible_p(pairs):g}; at least {needed} pairs are needed"
        )
    if pairs > MAX_PLAN_PAIRS:
        raise ValueError(f"plans are limited to {MAX_PLAN_PAIRS} pairs")


def min_detectable_effect(
    sigma: float, pairs: int, alpha: float = 0.05, power: float = 0.8, *, seed: int = 0
) -> float:
    """Smallest true mean difference the exact test detects with the wanted power.

    This solves for the effect using the exact test's own power (see `sign_flip_power`), not a
    normal approximation. With 6 pairs the test rejects only when all six differences share a
    sign, so the effect needed is about 1.79 sigma, not the 1.14 sigma a t-test would suggest.

    Args:
        sigma: Standard deviation of the per-pair differences.
        pairs: Number of pairs of blocks.
        alpha: Two-sided significance level.
        power: Wanted probability of detecting the effect, in (0, 1).
        seed: Seed for the simulated power when `pairs` is above the fewest possible.

    Raises:
        ValueError: If `sigma` is negative, `power` is outside (0, 1), `pairs` are too few to
            reach significance at all, or more than `MAX_PLAN_PAIRS`.
    """
    if sigma < 0:
        raise ValueError("sigma must not be negative")
    if not 0.0 < power < 1.0:
        raise ValueError("power must be strictly between 0 and 1")
    _check_plannable(pairs, alpha)
    if sigma == 0:
        return 0.0
    low, high = 0.0, MAX_EFFECT_SIGMAS
    for _ in range(POWER_BISECTION_STEPS):
        middle = (low + high) / 2.0
        if sign_flip_power(middle * sigma, sigma, pairs, alpha, seed=seed) >= power:
            high = middle
        else:
            low = middle
    return high * sigma
