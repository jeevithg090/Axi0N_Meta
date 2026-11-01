"""Opt-in check of the RTMPose estimator on real weights (review follow-up on P4-04).

Skipped unless `RULECAM_RTMPOSE_ONNX` (path to the exported model) and `RULECAM_RTMPOSE_SHA256`
(its pinned hash) are set. Run by hand: `uv run pytest -m gpu tests/pose/test_rtmpose_live.py`.
It checks the output structure and records the frame rate against the 5 to 10 fps spec. A blank
frame has no person in it, so it says nothing about accuracy.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

import numpy as np
import pytest

from rulecam.pose import RtmposeOnnx

MIN_FPS = 5.0
RUNS = 20


@pytest.mark.gpu
def test_rtmpose_on_real_weights_returns_17_keypoints_at_the_spec_frame_rate(
    record_property: Callable[[str, object], None],
) -> None:
    path, pinned = os.environ.get("RULECAM_RTMPOSE_ONNX"), os.environ.get("RULECAM_RTMPOSE_SHA256")
    if not path or not pinned:
        pytest.skip("set RULECAM_RTMPOSE_ONNX and RULECAM_RTMPOSE_SHA256 to run on real weights")
    estimator = RtmposeOnnx.load(Path(path), sha256=pinned)
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    box = (200.0, 100.0, 360.0, 440.0)
    estimator.estimate(frame, box)  # warm-up
    start = perf_counter()
    for _ in range(RUNS):
        keypoints = estimator.estimate(frame, box)
    fps = RUNS / (perf_counter() - start)
    record_property("fps", round(fps, 1))
    assert len(keypoints) == 17
    assert all(math.isfinite(k.x) and math.isfinite(k.y) for k in keypoints)
    assert fps >= MIN_FPS, f"{fps:.1f} fps is under the {MIN_FPS:g} fps the spec needs"
