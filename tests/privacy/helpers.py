from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

import numpy as np
from numpy.typing import NDArray

from rulecam.contracts import CandidateEvent, Keypoint, Severity
from rulecam.privacy import FrameBundle, PersonRender
from tests.fakes import EPOCH
from tests.pose.helpers import NAMES, STANDING, keypoints

HEIGHT, WIDTH = 300, 200
BACKGROUND = (50, 60, 70)  # BGR
SENTINEL = (255, 0, 255)  # a colour nothing in the renderer may produce


def scene(colour: tuple[int, int, int] = BACKGROUND) -> NDArray[np.uint8]:
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    frame[:] = colour
    return frame


def block_mask(x0: int, x1: int, y0: int, y1: int) -> NDArray[np.bool_]:
    mask = np.zeros((HEIGHT, WIDTH), dtype=np.bool_)
    mask[y0:y1, x0:x1] = True
    return mask


def person(
    track_id: str = "t1",
    pose: Mapping[str, tuple[float, float]] | None = None,
    ppe: Mapping[str, bool | None] | None = None,
) -> PersonRender:
    return PersonRender(track_id=track_id, keypoints=keypoints(pose or STANDING), ppe=ppe or {})


def stretched() -> Mapping[str, tuple[float, float]]:
    """STANDING with longer arms and legs: same torso, different proportions."""
    torso_top = {n: p for n, p in STANDING.items() if n in {"l_shoulder", "r_shoulder", "nose"}}
    out: dict[str, tuple[float, float]] = dict(STANDING)
    for name, (x, y) in STANDING.items():
        if name in torso_top or name.endswith(("_hip", "_eye", "_ear")):
            continue
        out[name] = (100 + (x - 100) * 1.4, 160 + (y - 160) * 1.4)
    return out


def event(event_id: str = "ev-1", seconds: float = 5.0) -> CandidateEvent:
    return CandidateEvent(
        event_id=event_id,
        rule_id="ppe-helmet",
        camera_id="cam1",
        ts_start=EPOCH,
        ts_end=EPOCH + timedelta(seconds=seconds),
        severity=Severity.MEDIUM,
        predicate_trace={},
        strip_paths=(),
        track_window_hash="h",
        plan_version_hash="p",
        rule_ir_hash="r",
    )


class SentinelSource:
    """Frames that are background everywhere except a sentinel-coloured person block."""

    def __init__(self) -> None:
        self.asked: list[datetime] = []

    def bundle(self, camera_id: str, ts: datetime) -> FrameBundle | None:
        self.asked.append(ts)
        frame = scene()
        mask = block_mask(70, 130, 80, 265)
        frame[mask] = SENTINEL
        face = block_mask(92, 108, 70, 90)
        return FrameBundle(
            frame=frame,
            people=[person(ppe={"helmet": True})],
            person_masks=[mask],
            face_masks=[face],
        )


__all__ = ["NAMES", "Keypoint"]
