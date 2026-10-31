"""Unit tests for statistical confidence intervals (task P2-04)."""

from __future__ import annotations

import pytest

from rulecam.stats.intervals import wilson_interval


def test_wilson_interval_known_values_10_of_12() -> None:
    """Verify Wilson 95% bound for 10 successes in 12 trials yields [0.552, 0.953]."""
    lb, ub = wilson_interval(10, 12, confidence=0.95)
    assert round(lb, 3) == 0.552
    assert round(ub, 3) == 0.953


def test_wilson_interval_zero_and_perfect_successes() -> None:
    """Verify boundary conditions for 0 successes and all successes."""
    lb0, ub0 = wilson_interval(0, 10, confidence=0.95)
    assert lb0 == 0.0
    assert ub0 > 0.0
    assert ub0 < 0.35

    lb10, ub10 = wilson_interval(10, 10, confidence=0.95)
    assert lb10 > 0.65
    assert lb10 < 1.0
    assert ub10 == 1.0


def test_wilson_interval_zero_trials() -> None:
    """Zero trials returns [0.0, 1.0]."""
    lb, ub = wilson_interval(0, 0)
    assert lb == 0.0
    assert ub == 1.0


def test_wilson_interval_invalid_counts_raises_value_error() -> None:
    """Invalid arguments raise ValueError."""
    with pytest.raises(ValueError, match="Invalid counts"):
        wilson_interval(-1, 10)
    with pytest.raises(ValueError, match="Invalid counts"):
        wilson_interval(5, -1)
    with pytest.raises(ValueError, match="Invalid counts"):
        wilson_interval(11, 10)


def test_wilson_interval_custom_confidence() -> None:
    """Custom confidence levels invoke ppf and compute correctly."""
    lb90, ub90 = wilson_interval(10, 12, confidence=0.90)
    lb99, ub99 = wilson_interval(10, 12, confidence=0.99)
    # Higher confidence means wider interval (lower lb, higher ub)
    assert lb99 < lb90
    assert ub99 > ub90
