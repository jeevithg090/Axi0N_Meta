"""Seeded resampling statistics (task P4-08b)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
from numpy.typing import NDArray

Statistic = Literal["mean", "median"]


def _as_array(values: Sequence[float]) -> NDArray[np.float64]:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or array.size == 0:
        raise ValueError("values must be a non-empty one-dimensional sequence")
    return array


def bootstrap_ci(
    values: Sequence[float],
    *,
    seed: int,
    statistic: Statistic = "mean",
    n_resamples: int = 2000,
    level: float = 0.95,
) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean or median.

    Args:
        values: The observations.
        seed: Seed for the resampling; the same seed gives the same interval.
        statistic: `mean` or `median`.
        n_resamples: Number of bootstrap resamples.
        level: Interval level, strictly between 0 and 1.

    Returns:
        `(lower, upper)`.

    Raises:
        ValueError: If `values` is empty or `level` is outside (0, 1).
    """
    array = _as_array(values)
    if not 0.0 < level < 1.0:
        raise ValueError("level must be strictly between 0 and 1")
    rng = np.random.default_rng(seed)
    resamples = array[rng.integers(0, array.size, size=(n_resamples, array.size))]
    stats = resamples.mean(axis=1) if statistic == "mean" else np.median(resamples, axis=1)
    tail = (1.0 - level) / 2.0
    lower, upper = np.quantile(stats, [tail, 1.0 - tail])
    return float(lower), float(upper)


def moving_window_pvalue(
    window: Sequence[float], baseline_segments: Sequence[Sequence[float]]
) -> float | None:
    """One-sided p-value that a window's mean is unusually high for a run of its length.

    Waits at a queue cluster in time, so shuffling cases between window and baseline understates
    the spread of a contiguous window's mean and flags quiet stations too often. Instead the
    window's mean is compared with the mean of every contiguous run of the same length inside the
    baseline. Under no change the window is just one more such run.

    Args:
        window: Values in the window under test, in time order.
        baseline_segments: Contiguous stretches of values outside the window, each in time order
            (for example, before and after it). Runs never straddle two segments.

    The runs overlap, so they are not independent draws: the p-value is only nominal, and the
    Benjamini-Hochberg control built on it is only approximate. `scripts/simulate_bottleneck_
    detection.py` measures how far off it is.

    Returns:
        `(1 + runs at least as high) / (1 + runs)`, or None if no baseline segment is as long as
        the window. The smallest value it can give is `1 / (1 + runs)`, so a short baseline cannot
        produce a small p-value.

    Raises:
        ValueError: If `window` is empty.
    """
    target = _as_array(window)
    length = target.size
    observed = float(target.mean())
    runs = higher = 0
    for segment in baseline_segments:
        if len(segment) < length:
            continue
        values = np.asarray(segment, dtype=np.float64)
        cumulative = np.concatenate([[0.0], np.cumsum(values)])
        means = (cumulative[length:] - cumulative[:-length]) / length
        runs += means.size
        higher += int(np.sum(means >= observed - 1e-12))
    if runs == 0:
        return None
    return (1 + higher) / (1 + runs)
