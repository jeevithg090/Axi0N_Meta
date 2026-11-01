from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulecam.advisor.stats import Stratum, mix_rate_decomposition, strata_from_samples

unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
rate = st.floats(min_value=-100.0, max_value=100.0, allow_nan=False)


@given(
    st.lists(st.tuples(unit, rate, unit, rate), min_size=1, max_size=6),
)
def test_mix_and_rate_parts_sum_to_the_total_gap(rows: list[tuple[float, ...]]) -> None:
    strata = [Stratum(f"s{i}", *row) for i, row in enumerate(rows)]
    result = mix_rate_decomposition(strata)
    assert result.rate + result.mix == pytest.approx(result.total_gap, abs=1e-9)
    assert sum(result.by_stratum.values()) == pytest.approx(result.total_gap, abs=1e-9)


def test_pure_mix_shift_has_no_rate_effect() -> None:
    period_a = {"early": [1.0, 1.0], "late": [3.0, 3.0]}
    period_b = {"early": [1.0], "late": [3.0, 3.0, 3.0]}
    result = mix_rate_decomposition(strata_from_samples(period_a, period_b))
    assert result.total_gap == pytest.approx(0.5)
    assert result.rate == pytest.approx(0.0)
    assert result.mix == pytest.approx(0.5)


def test_pure_rate_change_has_no_mix_effect() -> None:
    period_a = {"early": [1.0, 1.0], "late": [3.0, 3.0]}
    period_b = {"early": [2.0, 2.0], "late": [4.0, 4.0]}
    result = mix_rate_decomposition(strata_from_samples(period_a, period_b))
    assert result.total_gap == pytest.approx(1.0)
    assert result.mix == pytest.approx(0.0)


def test_a_stratum_missing_from_one_period_gets_zero_share_there() -> None:
    strata = strata_from_samples({"a": [2.0]}, {"a": [2.0], "b": [4.0]})
    by_name = {s.name: s for s in strata}
    assert by_name["b"].share_a == 0.0
    assert by_name["b"].share_b == 0.5


def test_strata_from_samples_needs_data_in_both_periods() -> None:
    with pytest.raises(ValueError, match="both periods"):
        strata_from_samples({}, {"a": [1.0]})
