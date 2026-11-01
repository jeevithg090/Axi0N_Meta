from __future__ import annotations

import pytest

from rulecam.advisor.stats import active_period_bottleneck, mean_wait, merge_spans, utilisation


def test_merge_spans() -> None:
    assert merge_spans([(5, 8), (0, 3), (2, 4), (4, 4.5), (9, 9)]) == [(0, 4.5), (5, 8)]


def test_utilisation_counts_union_inside_the_window() -> None:
    assert utilisation([(0, 30), (20, 40), (90, 200)], (0, 100)) == pytest.approx(0.5)
    assert utilisation([], (0, 100)) == 0.0
    with pytest.raises(ValueError, match="window"):
        utilisation([], (5, 5))


def test_mean_wait() -> None:
    assert mean_wait([2.0, 4.0]) == 3.0
    assert mean_wait([]) is None


def test_the_station_active_longest_is_the_bottleneck() -> None:
    shares = active_period_bottleneck({"A": [(0, 60)], "B": [(30, 90)]}, (0, 100))
    assert shares["A"] == pytest.approx(0.6)
    assert shares["B"] == pytest.approx(0.3)


def test_a_period_that_began_before_the_window_still_counts_from_its_true_start() -> None:
    shares = active_period_bottleneck({"A": [(-50, 10)], "B": [(0, 60)]}, (0, 100))
    assert shares["A"] == pytest.approx(0.1)
    assert shares["B"] == pytest.approx(0.5)


def test_ties_go_to_the_lower_station_id() -> None:
    shares = active_period_bottleneck({"dock_2": [(0, 50)], "dock_1": [(0, 50)]}, (0, 100))
    assert shares == {"dock_2": 0.0, "dock_1": pytest.approx(0.5)}


def test_bottleneck_rejects_an_empty_window() -> None:
    with pytest.raises(ValueError, match="window"):
        active_period_bottleneck({"A": []}, (3, 3))
