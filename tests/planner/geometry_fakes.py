"""Synthetic scenes for the planner geometry tests: a floor homography and SAM detections."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from rulecam.contracts import Detection, Mask
from rulecam.perception.masks import encode_rle
from tests.fakes import EPOCH

WIDTH, HEIGHT = 640, 480
# Ground metres -> image pixels for a camera looking down at the floor at an angle.
GROUND_TO_IMAGE = np.array([[120.0, 10.0, 160.0], [0.0, 60.0, 300.0], [0.0, 0.04, 1.0]])
PALLET_M = ((0.0, 0.0), (1.219, 0.0), (1.219, 1.016), (0.0, 1.016))


def to_image(points_m: list[tuple[float, float]] | tuple[tuple[float, float], ...]) -> np.ndarray:
    """Project ground points (metres) to pixels with `GROUND_TO_IMAGE`."""
    pts = np.column_stack([np.asarray(points_m, dtype=np.float64), np.ones(len(points_m))])
    mapped = pts @ GROUND_TO_IMAGE.T
    projected: np.ndarray = mapped[:, :2] / mapped[:, 2:3]
    return projected


def pallet_detection(camera_id: str, *, confidence: float = 0.9) -> Detection:
    """A SAM detection of a 48x40 in pallet lying on the floor, with a real COCO RLE mask."""
    corners = to_image(PALLET_M)
    mask = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    cv2.fillPoly(mask, [np.round(corners).astype(np.int32)], 1)
    xs, ys = corners[:, 0], corners[:, 1]
    box = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
    return detection(camera_id, "wooden pallet", box, rle=encode_rle(mask), confidence=confidence)


def detection(
    camera_id: str,
    concept: str,
    box: tuple[float, float, float, float],
    *,
    rle: str = "",
    confidence: float = 0.9,
) -> Detection:
    """A SAM detection with a box (and a mask when `rle` is given)."""
    return Detection(
        camera_id=camera_id,
        frame_idx=0,
        ts=EPOCH,
        concept=concept,
        object_id=1,
        mask=Mask(rle=rle, box_xyxy=box, confidence=confidence),
    )


def write_frames(directory: Path, camera_id: str, n: int = 30) -> tuple[Path, ...]:
    """Write `n` blank WIDTH x HEIGHT frames for a camera."""
    frames = tuple(directory / f"{camera_id}_{i:02d}.jpg" for i in range(n))
    image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for frame in frames:
        cv2.imwrite(str(frame), image)
    return frames


SINK_BOX = (300.0, 120.0, 400.0, 200.0)
PERSON_BOX = (100.0, 150.0, 160.0, 330.0)
FORKLIFT_BOX = (350.0, 200.0, 520.0, 380.0)


class SceneSam:
    """Fake SAM 3.1: a fixed scene; with `pallet=False` the pallet is gone."""

    def __init__(self, *, pallet: bool = True) -> None:
        self._pallet = pallet
        self.calls = 0

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        self.calls += 1
        if concept == "wooden pallet":
            return [pallet_detection(camera_id)] if self._pallet else []
        boxes = {"sink": SINK_BOX, "person": PERSON_BOX, "forklift": FORKLIFT_BOX}
        box = boxes.get(concept)
        return [detection(camera_id, concept, box)] if box else []
