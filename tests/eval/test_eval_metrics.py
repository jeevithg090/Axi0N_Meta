"""Metric math of the eval harness: temporal IoU matching, rates, percentiles, cost."""

from __future__ import annotations

import pytest

from rulecam.eval import metrics as m
from rulecam.eval.metrics import Interval


def test_temporal_iou_basic_cases() -> None:
    assert m.temporal_iou(Interval(0, 10), Interval(0, 10)) == 1.0
    assert m.temporal_iou(Interval(0, 10), Interval(5, 15)) == pytest.approx(5 / 15)
    assert m.temporal_iou(Interval(0, 4), Interval(6, 9)) == 0.0
    assert m.temporal_iou(Interval(0, 4), Interval(4, 9)) == 0.0  # touching only
    assert m.temporal_iou(Interval(3, 3), Interval(3, 3)) == 0.0  # two instants
    assert m.temporal_iou(Interval(2, 2), Interval(0, 4)) == 0.0  # an instant inside


def test_widen_pads_short_intervals_around_their_centre() -> None:
    assert m.widen(Interval(5, 5), 1.0) == Interval(4.5, 5.5)
    assert m.widen(Interval(0.2, 0.2), 1.0) == Interval(0.0, 0.7)  # never before the clip
    assert m.widen(Interval(1, 4), 1.0) == Interval(1, 4)


def test_merge_episodes_joins_overlaps_and_small_gaps() -> None:
    merged = m.merge_episodes(
        [Interval(10, 11), Interval(0, 1), Interval(2, 3), Interval(20, 21)], gap_s=1.0
    )
    assert merged == [Interval(0, 3), Interval(10, 11), Interval(20, 21)]
    assert m.merge_episodes([]) == []


def test_match_is_one_to_one_and_prefers_the_highest_iou() -> None:
    events = [Interval(0, 10), Interval(20, 30)]
    alerts = [Interval(1, 9), Interval(0, 10), Interval(21, 29), Interval(50, 51)]
    matching = m.match_events(events, alerts, iou=0.3)
    assert {(x.event, x.alert) for x in matching.matches} == {(0, 1), (1, 2)}
    assert (matching.tp, matching.fp, matching.fn) == (2, 2, 0)


def test_match_below_threshold_counts_as_miss_and_false_alert() -> None:
    matching = m.match_events([Interval(0, 10)], [Interval(8, 12)], iou=0.3)
    assert (matching.tp, matching.fp, matching.fn) == (0, 1, 1)
    exactly = m.match_events([Interval(0, 10)], [Interval(0, 3)], iou=0.3)
    assert exactly.tp == 1  # IoU 0.3 counts


def test_match_with_nothing_on_either_side() -> None:
    assert m.match_events([], []).tp == 0
    nothing_found = m.match_events([Interval(0, 1)], [])
    assert (nothing_found.fp, nothing_found.fn) == (0, 1)
    only_false = m.match_events([], [Interval(0, 1)])
    assert (only_false.fp, only_false.fn) == (1, 0)


def test_precision_and_recall_edge_cases() -> None:
    assert m.precision(0, 0) is None  # nothing alerted: undefined, not 0 or 1
    assert m.recall(0, 0) is None  # nothing to find
    assert m.precision(3, 1) == 0.75
    assert m.recall(0, 4) == 0.0
    assert m.ratio(1, 0) is None


def test_percentile_nearest_rank() -> None:
    assert m.percentile([], 95) is None
    assert m.percentile([4.0], 95) == 4.0
    values = [float(v) for v in range(1, 21)]
    assert m.percentile(values, 95) == 19.0
    assert m.percentile(values, 100) == 20.0
    assert m.percentile([3.0, 1.0, 2.0], 50) == 2.0


def test_decisiveness_counts_missing_verdicts_as_not_decisive() -> None:
    assert m.decisiveness(["violation", "unsure", None, "no_violation"]) == (2, 4)
    assert m.decisiveness([]) == (0, 0)


def test_overlaps_any() -> None:
    events = [Interval(5, 8)]
    assert m.overlaps_any(Interval(8, 9), events)
    assert not m.overlaps_any(Interval(9, 10), events)


def test_cost_per_camera_hour() -> None:
    # 3,000 billed frames in half an hour: $0.60 of SAM -> $1.20 per camera-hour.
    assert m.usd_per_camera_hour(3000, 0.5) == pytest.approx(1.2)
    assert m.usd_per_camera_hour(0, 1.0, gpu_usd_per_hour=0.8, cameras_per_gpu=2) == 0.4
    assert m.usd_per_camera_hour(100, 0.0) is None
    assert m.sam_usd(1000) == pytest.approx(0.2)
