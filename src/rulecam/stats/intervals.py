"""Statistical intervals and confidence bounds (task P2-04)."""

from __future__ import annotations

import math

from scipy.stats import norm  # type: ignore[import-untyped]

Z_95 = 1.959963984540054  # 95% two-sided normal quantile (alpha=0.05)


def wilson_interval(
    successes: int,
    total: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Calculate the Wilson score confidence interval for a binomial proportion.

    Args:
        successes: Number of successful detections/events (0 <= successes <= total).
        total: Total number of trials/frames (total >= 0).
        confidence: Two-sided confidence level (default: 0.95).

    Returns:
        (lower_bound, upper_bound) clamped to [0.0, 1.0].

    Raises:
        ValueError: If successes < 0, total < 0, or successes > total.
    """
    if successes < 0 or total < 0 or successes > total:
        msg = f"Invalid counts: successes={successes}, total={total}"
        raise ValueError(msg)
    if total == 0:
        return 0.0, 1.0

    z = Z_95 if math.isclose(confidence, 0.95) else float(norm.ppf(1.0 - (1.0 - confidence) / 2.0))

    p_hat = successes / total
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p_hat + z2 / (2.0 * total)) / denom
    spread = (z / denom) * math.sqrt((p_hat * (1.0 - p_hat) / total) + (z2 / (4.0 * total * total)))

    lower = 0.0 if successes == 0 else max(0.0, min(1.0, center - spread))
    upper = 1.0 if successes == total else max(0.0, min(1.0, center + spread))
    return lower, upper
