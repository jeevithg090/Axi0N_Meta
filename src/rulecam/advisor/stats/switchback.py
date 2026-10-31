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


def min_detectable_effect(
    sigma: float, pairs: int, alpha: float = 0.05, power: float = 0.8
) -> float:
    """Smallest true mean difference the trial is likely to detect (normal approximation).

    This is `(z(1 - alpha/2) + z(power)) * sigma / sqrt(pairs)`. The exact test is a little more
    conservative, so treat it as a planning figure.

    Args:
        sigma: Standard deviation of the per-pair differences.
        pairs: Number of pairs of blocks.
        alpha: Two-sided significance level.
        power: Wanted probability of detecting the effect.

    Raises:
        ValueError: If `sigma` is negative, or `pairs` are too few to reach significance at all.
    """
    if sigma < 0:
        raise ValueError("sigma must not be negative")
    if smallest_possible_p(pairs) > alpha:
        needed = minimum_pairs(alpha)
        raise ValueError(
            f"{pairs} pairs can never reach p <= {alpha}: the smallest possible p is "
            f"{smallest_possible_p(pairs):g}; at least {needed} pairs are needed"
        )
    normal = NormalDist()
    z = normal.inv_cdf(1.0 - alpha / 2.0) + normal.inv_cdf(power)
    return z * sigma / math.sqrt(pairs)
