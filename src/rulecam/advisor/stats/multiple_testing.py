"""Benjamini-Hochberg false-discovery-rate control (task P4-08b)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class BenjaminiHochberg:
    """Outcome of a Benjamini-Hochberg procedure, in the order the p-values were given."""

    rejected: tuple[bool, ...]
    adjusted: tuple[float, ...]


def benjamini_hochberg(p_values: Sequence[float], q: float = 0.05) -> BenjaminiHochberg:
    """Control the false discovery rate at `q` over a family of tests.

    Args:
        p_values: One p-value per test, each in [0, 1].
        q: Target false discovery rate.

    Returns:
        Which tests are rejected (discoveries) and the adjusted p-values.

    Raises:
        ValueError: If a p-value is outside [0, 1] or `q` is outside (0, 1).
    """
    if not 0.0 < q < 1.0:
        raise ValueError("q must be strictly between 0 and 1")
    if any(not 0.0 <= p <= 1.0 for p in p_values):
        raise ValueError("p-values must be in [0, 1]")
    count = len(p_values)
    order = sorted(range(count), key=lambda i: p_values[i])
    adjusted = [0.0] * count
    running = 1.0
    for rank in range(count, 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * count / rank)
        adjusted[index] = running
    return BenjaminiHochberg(rejected=tuple(a <= q for a in adjusted), adjusted=tuple(adjusted))
