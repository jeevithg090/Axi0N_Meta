"""The skeleton wipe: a bar sweeps across a frame from raw to skeleton (task P4-05).

The composite shows raw footage, so it is made only from consenting teammates' clips.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]

BAR_COLOUR = (255, 255, 255)
BAR_WIDTH = 4


class ConsentError(RuntimeError):
    """The wipe was asked for without confirmed consent from everyone in the raw footage."""


def wipe_frame(raw: Frame, anonymous: Frame, progress: float) -> Frame:
    """Composite one wipe frame: skeleton to the left of the bar, raw to the right.

    Args:
        raw: The raw frame.
        anonymous: The skeleton render of the same frame.
        progress: How far the bar has swept, from 0 (all raw) to 1 (all skeleton).

    Returns:
        The composite with a white bar at the sweep position.

    Raises:
        ValueError: If the frames differ in shape or `progress` is outside [0, 1].
    """
    if raw.shape != anonymous.shape:
        raise ValueError("raw and anonymous frames must have the same shape")
    if not 0.0 <= progress <= 1.0:
        raise ValueError("progress must be between 0 and 1")
    width = raw.shape[1]
    edge = round(progress * width)
    out = raw.copy()
    out[:, :edge] = anonymous[:, :edge]
    start = min(max(edge - BAR_WIDTH // 2, 0), width - BAR_WIDTH)
    out[:, start : start + BAR_WIDTH] = BAR_COLOUR
    return out


def write_wipe_video(
    raw_frames: Sequence[Frame],
    anonymous_frames: Sequence[Frame],
    path: Path,
    *,
    consent_confirmed: bool,
    fps: float = 15.0,
) -> Path:
    """Write the wipe as an MP4, sweeping the bar evenly from the first frame to the last.

    Args:
        raw_frames: The raw frames.
        anonymous_frames: The matching skeleton renders.
        path: Where to write the video.
        consent_confirmed: True only if everyone in the raw footage has given consent (S-02).
        fps: Frames per second.

    Returns:
        `path`.

    Raises:
        ConsentError: If consent is not confirmed.
        ValueError: If there are no frames, or the two sequences differ in length.
    """
    if not consent_confirmed:
        raise ConsentError("the wipe shows raw footage: confirm consent from everyone in it")
    if not raw_frames or len(raw_frames) != len(anonymous_frames):
        raise ValueError("need the same, non-zero number of raw and anonymous frames")
    height, width = raw_frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise OSError(f"could not open {path} for writing")
    last = max(len(raw_frames) - 1, 1)
    try:
        for i, (raw, anon) in enumerate(zip(raw_frames, anonymous_frames, strict=True)):
            writer.write(wipe_frame(raw, anon, i / last))
    finally:
        writer.release()
    return path
