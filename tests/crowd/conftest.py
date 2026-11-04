"""Shared fakes for the crowd tests: recorded frames that move, SAM that finds set boxes."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from rulecam.ask.vision import Frame, FrameGrabber
from rulecam.contracts import Calibration, Chunk, Detection, Mask
from tests.fakes import EPOCH, make_chunk

WIDTH, HEIGHT = 400, 300
SHIFT_PX_PER_FRAME = 2  # the scene drifts right by this much every frame (10 fps chunks)


def scene(index: int) -> np.ndarray:
    """A textured frame; frame `index` is the scene moved right by `index * SHIFT` pixels."""
    rng = np.random.default_rng(7)
    wide = cv2.GaussianBlur(rng.integers(0, 255, (HEIGHT, WIDTH + 400), dtype=np.uint8), (5, 5), 0)
    start = 200 - (index * SHIFT_PX_PER_FRAME) % 200
    return cv2.cvtColor(np.ascontiguousarray(wide[:, start : start + WIDTH]), cv2.COLOR_GRAY2BGR)


class Chunks:
    """Ten 3 s chunks of one camera, 10 fps."""

    def __init__(self, camera_id: str = "gate") -> None:
        self.recorded = [make_chunk(camera_id, i) for i in range(10)]

    def last_seen(self) -> dict[str, datetime]:
        return {self.recorded[-1].camera_id: self.recorded[-1].start_ts}

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        return [c for c in self.recorded if c.camera_id == camera_id]


def write_scene(chunk: Path, indices: Sequence[int], out_dir: Path) -> None:
    """Decode stand-in: frame k of chunk n is scene(30 n + k)."""
    number = int(chunk.stem.rsplit("_", 1)[1])
    cv2.imwrite(str(out_dir / "1.jpg"), scene(number * 30 + indices[0]))


@pytest.fixture
def grabber(tmp_path: Path) -> FrameGrabber:
    return FrameGrabber(Chunks(), tmp_path / "frames", extract=write_scene)


@pytest.fixture
def frame_at(tmp_path: Path) -> Callable[[int, datetime], Frame]:
    def make(index: int, at: datetime) -> Frame:
        path = tmp_path / f"scene-{index}.jpg"
        cv2.imwrite(str(path), scene(index))
        return Frame(camera_id="gate", at=at, path=path)

    return make


class Sam:
    """SAM 3.1 stand-in: the same boxes (in the given image's pixels) on every call."""

    def __init__(self, boxes: Sequence[tuple[float, float, float, float]]) -> None:
        self.boxes = list(boxes)
        self.calls: list[tuple[str, str]] = []

    def segment_image(
        self, frame: Path, concept: str, *, camera_id: str = "x", include_confidence: bool = True
    ) -> list[Detection]:
        self.calls.append((frame.name, concept))
        return [
            Detection(
                camera_id=camera_id,
                frame_idx=0,
                ts=EPOCH,
                concept=concept,
                object_id=k,
                mask=Mask(rle="", box_xyxy=box, confidence=0.9),
            )
            for k, box in enumerate(self.boxes)
        ]


def top_down(scale_m_per_px: float = 0.01, camera_id: str = "gate") -> Calibration:
    """A camera looking straight down: every pixel is `scale` metres on the ground."""
    return Calibration(
        camera_id=camera_id,
        homography=((scale_m_per_px, 0.0, 0.0), (0.0, scale_m_per_px, 0.0), (0.0, 0.0, 1.0)),
        method="clicks4",
        err_m=0.1,
    )


def wait_for(check: Callable[[], bool], seconds: float = 5.0) -> None:
    """Poll until `check()` holds (the memories work on their own threads)."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        if check():
            return
        time.sleep(0.02)
    msg = "timed out waiting for the worker thread"
    raise AssertionError(msg)
