"""Confidence intervals for proportions (task P2-04; P4 reuses them for evaluation)."""

from __future__ import annotations

import math
from statistics import NormalDist


def wilson_interval(
    successes: int,
    total: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Return the Wilson score interval for a binomial proportion.

    With `p = successes / total`, `n = total` and `z` the two-sided normal quantile:

        centre = (p + z²/2n) / (1 + z²/n)
        half   = z / (1 + z²/n) · sqrt(p(1 - p)/n + z²/4n²)

    The bounds are exactly 0 when `successes == 0` and exactly 1 when `successes == total`; they
    are pinned there so float rounding cannot leak past them.

    Args:
        successes: Number of successes, `0 <= successes <= total`.
        total: Number of trials, `total >= 0`.
        confidence: Two-sided confidence level in (0, 1).

    Returns:
        `(lower, upper)` in [0, 1]. With no trials nothing is known, so `(0.0, 1.0)`.

    Raises:
        ValueError: If the counts are inconsistent or `confidence` is not in (0, 1).
    """
    if successes < 0 or total < 0 or successes > total:
        msg = f"Invalid counts: successes={successes}, total={total}"
        raise ValueError(msg)
    if not 0.0 < confidence < 1.0:
        msg = f"confidence must be in (0, 1), got {confidence}"
        raise ValueError(msg)
    if total == 0:
        return 0.0, 1.0

    z = NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)
    p_hat = successes / total
    z2 = z * z
    denom = 1.0 + z2 / total
    centre = (p_hat + z2 / (2.0 * total)) / denom
    half = (z / denom) * math.sqrt(p_hat * (1.0 - p_hat) / total + z2 / (4.0 * total * total))

    lower = 0.0 if successes == 0 else max(0.0, centre - half)
    upper = 1.0 if successes == total else min(1.0, centre + half)
    return lower, upper
