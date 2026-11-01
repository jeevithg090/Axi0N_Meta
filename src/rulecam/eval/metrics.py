"""Pure metric math for the eval harness (WS0): temporal matching, rates, latency, cost.

Alerts are matched to labelled events by **temporal IoU** (intersection over union of their
clip-second intervals), greedily from the highest IoU, one-to-one, keeping pairs at or above the
threshold (0.3 by default, PLAN-9.5 §3). Before matching, alerts of one rule on one clip that
overlap or nearly touch are merged into one alert episode (the runtime may split one violation
over several windows), and an episode shorter than `min_alert_s` is widened around its centre
(the engine reports an instant as a zero-length interval, which could never reach any IoU).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rulecam.clients.meta_hosted.sam_cost import USD_PER_VIDEO_FRAME

DEFAULT_IOU = 0.3
DEFAULT_MERGE_GAP_S = 3.0  # one chunk
DEFAULT_MIN_ALERT_S = 1.0
UNSURE = "unsure"
SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class Interval:
    """A closed interval of clip seconds."""

    start: float
    end: float

    @property
    def length(self) -> float:
        """Its duration in seconds (never negative)."""
        return max(0.0, self.end - self.start)


def temporal_iou(a: Interval, b: Interval) -> float:
    """Return the IoU of two intervals; 0 when they do not overlap or both are instants."""
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    union = a.length + b.length - inter
    return inter / union if union > 0 else 0.0


def widen(interval: Interval, min_length: float) -> Interval:
    """Widen an interval around its centre to at least `min_length` seconds."""
    if interval.length >= min_length:
        return interval
    centre = (interval.start + interval.end) / 2
    return Interval(max(0.0, centre - min_length / 2), centre + min_length / 2)


def merge_episodes(
    intervals: Iterable[Interval], *, gap_s: float = DEFAULT_MERGE_GAP_S
) -> list[Interval]:
    """Merge intervals that overlap or are at most `gap_s` apart, in time order."""
    merged: list[Interval] = []
    for current in sorted(intervals, key=lambda i: (i.start, i.end)):
        if merged and current.start <= merged[-1].end + gap_s:
            last = merged[-1]
            merged[-1] = Interval(last.start, max(last.end, current.end))
        else:
            merged.append(current)
    return merged


@dataclass(frozen=True)
class Match:
    """One alert episode matched to one labelled event."""

    event: int
    alert: int
    iou: float


@dataclass(frozen=True)
class Matching:
    """The result of matching alert episodes to events on one clip for one rule."""

    matches: tuple[Match, ...]
    n_alerts: int
    n_events: int

    @property
    def tp(self) -> int:
        """Matched pairs."""
        return len(self.matches)

    @property
    def fp(self) -> int:
        """Alert episodes with no event."""
        return self.n_alerts - self.tp

    @property
    def fn(self) -> int:
        """Events with no alert episode."""
        return self.n_events - self.tp


def match_events(
    events: Sequence[Interval], alerts: Sequence[Interval], *, iou: float = DEFAULT_IOU
) -> Matching:
    """Greedily match alerts to events by descending temporal IoU (each used at most once)."""
    pairs = sorted(
        (
            (temporal_iou(event, alert), e, a)
            for e, event in enumerate(events)
            for a, alert in enumerate(alerts)
        ),
        key=lambda p: (-p[0], p[1], p[2]),
    )
    used_events: set[int] = set()
    used_alerts: set[int] = set()
    matches = []
    for score, e, a in pairs:
        if score < iou or score <= 0:
            break
        if e in used_events or a in used_alerts:
            continue
        used_events.add(e)
        used_alerts.add(a)
        matches.append(Match(event=e, alert=a, iou=score))
    return Matching(matches=tuple(matches), n_alerts=len(alerts), n_events=len(events))


def ratio(numerator: int, denominator: int) -> float | None:
    """Return `numerator / denominator`, or `None` when the denominator is 0 (undefined)."""
    return numerator / denominator if denominator else None


def precision(tp: int, fp: int) -> float | None:
    """TP / (TP + FP); `None` when nothing was alerted."""
    return ratio(tp, tp + fp)


def recall(tp: int, fn: int) -> float | None:
    """TP / (TP + FN); `None` when there was nothing to find."""
    return ratio(tp, tp + fn)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Return the nearest-rank `q`-th percentile (0 < q <= 100), or `None` for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def decisiveness(verdicts: Iterable[str | None]) -> tuple[int, int]:
    """Return (decisive, total) over Tier 1 verdicts; a missing verdict counts as not decisive."""
    labels = list(verdicts)
    return sum(1 for v in labels if v is not None and v != UNSURE), len(labels)


def overlaps_any(interval: Interval, events: Sequence[Interval]) -> bool:
    """Whether `interval` shares any time with one of `events` (touching counts)."""
    return any(interval.start <= e.end and e.start <= interval.end for e in events)


def sam_usd(billed_frames: int) -> float:
    """Hosted SAM video spend for `billed_frames` (sam_cost pricing)."""
    return billed_frames * USD_PER_VIDEO_FRAME


def usd_per_camera_hour(
    billed_frames: int,
    camera_hours: float,
    *,
    gpu_usd_per_hour: float = 0.0,
    cameras_per_gpu: float = 1.0,
) -> float | None:
    """Model spend per camera-hour: hosted SAM frames plus each camera's share of the GPUs.

    Args:
        billed_frames: Hosted SAM frames billed while the camera played.
        camera_hours: Camera time covered.
        gpu_usd_per_hour: Price of the GPU pods (SAM, Glimmer) per hour, all together.
        cameras_per_gpu: How many cameras share those pods.

    Returns:
        Dollars per camera-hour, or `None` when no camera time was covered.
    """
    if camera_hours <= 0:
        return None
    return sam_usd(billed_frames) / camera_hours + gpu_usd_per_hour / max(cameras_per_gpu, 1e-9)
