"""The six-frame evidence strip, rendered for a privacy mode (task P4-05, MP §7.5).

In standard mode a strip shows the real frames with person masks overlaid and faces blurred. In
anonymous and strict modes it shows skeleton renders on the clean plate only: the raw frame is
never written, and no path to one is ever returned.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from rulecam.contracts import CandidateEvent, PrivacyMode
from rulecam.privacy.background_plate import BackgroundPlate
from rulecam.privacy.face_mask import blur_faces
from rulecam.privacy.skeleton_render import PersonRender, render_skeletons

Frame = NDArray[np.uint8]
BoolMask = NDArray[np.bool_]

STRIP_FRAMES = 6
OVERLAY_ALPHA = 0.4
OVERLAY_COLOURS = ((255, 120, 0), (0, 160, 255), (200, 0, 200), (0, 200, 120))


@dataclass(frozen=True)
class FrameBundle:
    """Everything known about one camera at one moment."""

    frame: Frame
    people: Sequence[PersonRender]
    person_masks: Sequence[BoolMask]
    face_masks: Sequence[BoolMask]


class FrameSource(Protocol):
    """Gives the frame and detections for a camera at a time."""

    def bundle(self, camera_id: str, ts: datetime) -> FrameBundle | None:
        """Return the bundle, or None if that moment is no longer available."""
        ...


def strip_times(start: datetime, end: datetime, frames: int = STRIP_FRAMES) -> list[datetime]:
    """Evenly spaced moments from `start` to `end`, both included."""
    if frames < 1:
        raise ValueError("frames must be at least 1")
    if frames == 1:
        return [start]
    return [start + (end - start) * i / (frames - 1) for i in range(frames)]


def overlay_masks(frame: Frame, masks: Sequence[BoolMask]) -> Frame:
    """Tint each mask on a copy of the frame (standard-mode strips)."""
    result = frame.copy()
    for i, mask in enumerate(masks):
        colour = np.array(OVERLAY_COLOURS[i % len(OVERLAY_COLOURS)], dtype=np.float32)
        blended = frame[mask].astype(np.float32) * (1 - OVERLAY_ALPHA) + colour * OVERLAY_ALPHA
        result[mask] = blended.astype(np.uint8)
    return result


def render_strip(
    event: CandidateEvent,
    mode: PrivacyMode,
    source: FrameSource,
    plate: BackgroundPlate,
    out_dir: Path,
    *,
    frames: int = STRIP_FRAMES,
) -> tuple[str, ...]:
    """Render and save an event's strip for a privacy mode.

    Args:
        event: The event; its time span and camera pick the frames.
        mode: `standard` writes real frames with mask overlays and blurred faces. `anonymous` and
            `strict` write skeleton renders on the plate and never touch a raw frame.
        source: Where frames and detections come from.
        plate: The clean plate for this camera, kept up to date by the caller. Used only in
            anonymous and strict modes.
        out_dir: Directory for the PNG files, created if missing.
        frames: Number of frames in the strip.

    Returns:
        Paths of the files written, in time order. Moments the source cannot give are skipped.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for index, moment in enumerate(strip_times(event.ts_start, event.ts_end, frames)):
        bundle = source.bundle(event.camera_id, moment)
        if bundle is None:
            continue
        if mode is PrivacyMode.STANDARD:
            image = blur_faces(overlay_masks(bundle.frame, bundle.person_masks), bundle.face_masks)
        else:
            image = render_skeletons(plate.render(), bundle.people, mode)
        path = out_dir / f"{event.event_id}_{index}.png"
        if not cv2.imwrite(str(path), image):
            raise OSError(f"could not write {path}")
        paths.append(str(path))
    return tuple(paths)
