from __future__ import annotations

import pytest

from rulecam.advisor.stats import benjamini_hochberg


def test_all_rejected_when_each_clears_its_threshold() -> None:
    result = benjamini_hochberg([0.01, 0.04, 0.03, 0.005], q=0.05)
    assert result.rejected == (True, True, True, True)


def test_largest_p_value_is_not_rejected_and_adjusted_values_are_known() -> None:
    result = benjamini_hochberg([0.01, 0.02, 0.03, 0.2], q=0.05)
    assert result.rejected == (True, True, True, False)
    assert result.adjusted == pytest.approx((0.04, 0.04, 0.04, 0.2))


def test_order_of_input_is_preserved() -> None:
    result = benjamini_hochberg([0.2, 0.01, 0.03, 0.02], q=0.05)
    assert result.rejected == (False, True, True, True)


def test_nothing_rejected_and_empty_input() -> None:
    assert benjamini_hochberg([0.5, 0.6, 0.7]).rejected == (False, False, False)
    empty = benjamini_hochberg([])
    assert empty.rejected == ()
    assert empty.adjusted == ()


@pytest.mark.parametrize(("p_values", "q"), [([1.2], 0.05), ([-0.1], 0.05), ([0.5], 0.0)])
def test_rejects_invalid_arguments(p_values: list[float], q: float) -> None:
    with pytest.raises(ValueError, match=r"between|in \[0, 1\]"):
        benjamini_hochberg(p_values, q)
