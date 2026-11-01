from __future__ import annotations

import pytest

from rulecam.advisor.stats import hour_of_day_baseline, robust_z
from tests.advisor.dock_log import at

HOUR = 3600


def test_baseline_per_hour() -> None:
    stamps = [at(0), at(60), at(120), at(HOUR), at(HOUR + 60)]
    baseline = hour_of_day_baseline(stamps, [1.0, 2.0, 9.0, 5.0, 7.0])
    first, second = baseline[stamps[0].hour], baseline[stamps[3].hour]
    assert (first.median, first.mad, first.n) == (2.0, 1.0, 3)
    assert (second.median, second.mad, second.n) == (6.0, 1.0, 2)


def test_robust_z() -> None:
    baseline = hour_of_day_baseline([at(0), at(10), at(20)], [1.0, 2.0, 3.0])[at(0).hour]
    assert robust_z(2.0, baseline) == 0.0
    assert robust_z(4.0, baseline) == pytest.approx(2.0 / 1.4826)


def test_robust_z_is_undefined_without_spread() -> None:
    baseline = hour_of_day_baseline([at(0), at(10)], [2.0, 2.0])[at(0).hour]
    assert robust_z(5.0, baseline) is None


def test_baseline_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        hour_of_day_baseline([at(0)], [1.0, 2.0])
