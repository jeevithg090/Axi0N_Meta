from __future__ import annotations

import pytest

from rulecam.advisor.stats import bootstrap_ci, moving_window_pvalue

DATA = [4.0, 6.0, 5.0, 7.0, 3.0, 5.5, 4.5, 6.5]


def test_bootstrap_is_reproducible_by_seed() -> None:
    many = [((i * 37) % 101) / 7.0 for i in range(60)]
    assert bootstrap_ci(many, seed=3) == bootstrap_ci(many, seed=3)
    assert bootstrap_ci(many, seed=3) != bootstrap_ci(many, seed=4)


def test_bootstrap_brackets_the_mean_and_narrows_for_constant_data() -> None:
    lo, hi = bootstrap_ci(DATA, seed=1)
    assert lo < sum(DATA) / len(DATA) < hi
    assert bootstrap_ci([5.0] * 10, seed=1) == (5.0, 5.0)


def test_bootstrap_median_and_level() -> None:
    narrow = bootstrap_ci(DATA, seed=1, level=0.5)
    wide = bootstrap_ci(DATA, seed=1, level=0.99)
    assert wide[0] <= narrow[0] <= narrow[1] <= wide[1]
    lo, hi = bootstrap_ci(DATA, seed=1, statistic="median")
    assert 3.0 <= lo <= hi <= 7.0


@pytest.mark.parametrize("bad", [[], [[1.0, 2.0]]])
def test_bootstrap_rejects_bad_input(bad: list[object]) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        bootstrap_ci(bad, seed=1)  # type: ignore[arg-type]


def test_bootstrap_rejects_bad_level() -> None:
    with pytest.raises(ValueError, match="level"):
        bootstrap_ci(DATA, seed=1, level=1.0)


ALTERNATING = [1.0, 2.0] * 20  # every run of 5 has a mean of 1.4 or 1.6


def test_a_window_higher_than_every_baseline_run_gets_the_smallest_p() -> None:
    p = moving_window_pvalue([10.0] * 5, [ALTERNATING])
    assert p == pytest.approx(1 / 37)  # 36 runs of length 5 in 40 values


def test_a_typical_window_gets_a_large_p() -> None:
    assert moving_window_pvalue([1.0, 2.0, 1.0, 2.0, 1.0], [ALTERNATING]) == 1.0
    assert moving_window_pvalue([0.0] * 5, [ALTERNATING]) == 1.0


def test_runs_never_straddle_two_segments() -> None:
    # Only two runs of length 3 exist (one per segment); a straddling run would add four more.
    assert moving_window_pvalue([5.0] * 3, [[0.0] * 3, [10.0] * 3]) == pytest.approx(2 / 3)


def test_no_p_when_no_baseline_segment_is_as_long_as_the_window() -> None:
    assert moving_window_pvalue([1.0] * 5, [[1.0] * 4, []]) is None
    assert moving_window_pvalue([1.0] * 5, []) is None


def test_moving_window_rejects_an_empty_window() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        moving_window_pvalue([], [ALTERNATING])
