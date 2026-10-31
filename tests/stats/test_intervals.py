"""Unit tests for the Wilson score interval (task P2-04)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulecam.stats.intervals import wilson_interval


def test_known_value_10_of_12() -> None:
    lower, upper = wilson_interval(10, 12)
    assert (round(lower, 3), round(upper, 3)) == (0.552, 0.953)


def test_all_successes_pins_the_upper_bound_and_caps_the_lower_bound() -> None:
    lower, upper = wilson_interval(12, 12)
    assert upper == 1.0
    assert lower == pytest.approx(12 / (12 + 1.959963984540054**2))  # n / (n + z²)


def test_no_successes_pins_the_lower_bound() -> None:
    lower, upper = wilson_interval(0, 12)
    assert lower == 0.0
    assert upper == pytest.approx(1 - 12 / (12 + 1.959963984540054**2))


def test_zero_trials_is_uninformative() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


@pytest.mark.parametrize(("successes", "total"), [(-1, 10), (5, -1), (11, 10)])
def test_invalid_counts_raise(successes: int, total: int) -> None:
    with pytest.raises(ValueError, match="Invalid counts"):
        wilson_interval(successes, total)


@pytest.mark.parametrize("confidence", [0.0, 1.0, -0.5, 1.5])
def test_confidence_outside_the_open_unit_interval_raises(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        wilson_interval(5, 10, confidence=confidence)


def test_higher_confidence_widens_the_interval() -> None:
    lb90, ub90 = wilson_interval(10, 12, confidence=0.90)
    lb95, ub95 = wilson_interval(10, 12)
    lb99, ub99 = wilson_interval(10, 12, confidence=0.99)
    assert lb99 < lb95 < lb90
    assert ub90 < ub95 < ub99


@given(
    st.integers(min_value=1, max_value=500).flatmap(
        lambda n: st.tuples(st.integers(0, n), st.just(n))
    )
)
def test_interval_brackets_the_estimate_and_stays_in_the_unit_interval(
    counts: tuple[int, int],
) -> None:
    successes, total = counts
    lower, upper = wilson_interval(successes, total)
    assert 0.0 <= lower <= successes / total <= upper <= 1.0


@given(
    st.integers(min_value=1, max_value=200).flatmap(
        lambda n: st.tuples(st.integers(0, n - 1), st.just(n))
    )
)
def test_lower_bound_grows_with_successes(counts: tuple[int, int]) -> None:
    successes, total = counts
    assert wilson_interval(successes, total)[0] < wilson_interval(successes + 1, total)[0]
