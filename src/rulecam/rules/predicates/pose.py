"""The `pose` predicate over injected pose features (see `rulecam.rules.pose`)."""

from __future__ import annotations

import math

import numpy as np

from rulecam.contracts.rule_ir import PoseState
from rulecam.rules.ir import parse_ref
from rulecam.rules.scene import Scene
from rulecam.rules.signals import Signal, make_series


def pose(node: PoseState, scene: Scene) -> Signal:
    """Per-subject pose state; unknown (no adapter, or unreliable keypoints) evaluates false.

    The value is 1.0 or 0.0, or NaN when unknown (reported as `<path>.unknown` in traces).
    """
    concept, _ = parse_ref(node.object)
    series = {}
    for subject in scene.tracks_of(concept):
        if scene.pose is None:
            per_point = np.full(len(subject.track.points), np.nan)
        else:
            features = scene.pose
            per_point = np.array(
                [_flag(features(node.state, p)) for p in subject.track.points], dtype=np.float64
            )
        value = per_point[np.maximum(subject.src, 0)]
        value[subject.src < 0] = np.nan
        series[subject.track_id] = make_series(subject.start, value == 1.0, value, subject.present)
    return Signal(subject=concept, unit=None, series=series)


def _flag(result: bool | None) -> float:
    if result is None:
        return math.nan
    return 1.0 if result else 0.0
