"""Pose estimation for tracked people (task P4-04).

`RtmposeOnnx` is the CPU path (RTMPose, Apache-2.0). `PoseEstimator` is the seam where a Sapiens
worker on a GPU can plug in. Poses are estimated every few frames and filled between by linear
interpolation. Only person tracks should be passed in: this produces joints, never identity.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from rulecam.contracts import Keypoint, Track, TrackPoint
from rulecam.contracts.core import BoxXYXY

INPUT_W, INPUT_H = 192, 256
SIMCC_SPLIT_RATIO = 2.0
BOX_PADDING = 1.25
MEAN = np.array((123.675, 116.28, 103.53), dtype=np.float32)
STD = np.array((58.395, 57.12, 57.375), dtype=np.float32)

Frame = NDArray[np.uint8]


class PoseEstimator(Protocol):
    """Estimates COCO-17 keypoints for one person in a frame."""

    def estimate(self, frame: Frame, box_xyxy: BoxXYXY) -> tuple[Keypoint, ...]:
        """Return 17 keypoints in image pixels for the person inside `box_xyxy`."""
        ...


class OnnxSession(Protocol):
    """The part of an `onnxruntime.InferenceSession` this module uses."""

    def run(
        self, output_names: Sequence[str] | None, input_feed: Mapping[str, NDArray[np.float32]]
    ) -> Sequence[NDArray[np.float32]]:
        """Run the model and return its outputs."""
        ...


@dataclass(frozen=True)
class CropWindow:
    """The padded, fixed-aspect window of the image fed to the model, in image pixels."""

    x0: float
    y0: float
    width: float
    height: float


def crop_window(box_xyxy: BoxXYXY) -> CropWindow:
    """Grow a person box to the model's aspect ratio and pad it.

    Args:
        box_xyxy: The person's box in image pixels.

    Returns:
        A window centred on the box with aspect `INPUT_W / INPUT_H`, padded by `BOX_PADDING`.
    """
    x1, y1, x2, y2 = box_xyxy
    centre_x, centre_y = (x1 + x2) / 2, (y1 + y2) / 2
    width, height = x2 - x1, y2 - y1
    aspect = INPUT_W / INPUT_H
    if width > height * aspect:
        height = width / aspect
    else:
        width = height * aspect
    width, height = width * BOX_PADDING, height * BOX_PADDING
    return CropWindow(centre_x - width / 2, centre_y - height / 2, width, height)


def prepare_input(frame: Frame, window: CropWindow) -> NDArray[np.float32]:
    """Crop and normalise `window` into a `(1, 3, INPUT_H, INPUT_W)` model input.

    Args:
        frame: An RGB image, height x width x 3. Areas outside the frame become black.
        window: The crop from `crop_window`.

    Returns:
        The normalised float32 tensor.
    """
    scale_x, scale_y = INPUT_W / window.width, INPUT_H / window.height
    matrix = np.array(
        [[scale_x, 0.0, -window.x0 * scale_x], [0.0, scale_y, -window.y0 * scale_y]],
        dtype=np.float32,
    )
    crop = cv2.warpAffine(frame, matrix, (INPUT_W, INPUT_H), flags=cv2.INTER_LINEAR)
    normalised = (crop.astype(np.float32) - MEAN) / STD
    return np.ascontiguousarray(normalised.transpose(2, 0, 1)[np.newaxis])


def decode_simcc(
    simcc_x: NDArray[np.float32], simcc_y: NDArray[np.float32]
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Decode SimCC outputs into model-input pixel positions and scores.

    Args:
        simcc_x: Shape `(1, K, INPUT_W * SIMCC_SPLIT_RATIO)`.
        simcc_y: Shape `(1, K, INPUT_H * SIMCC_SPLIT_RATIO)`.

    Returns:
        Positions of shape `(K, 2)` in model-input pixels, and scores of shape `(K,)`: the lower
        of the two axis peaks.
    """
    x, y = simcc_x[0], simcc_y[0]
    positions = np.stack([x.argmax(axis=-1), y.argmax(axis=-1)], axis=-1) / SIMCC_SPLIT_RATIO
    scores = np.minimum(x.max(axis=-1), y.max(axis=-1))
    return positions.astype(np.float32), scores.astype(np.float32)


def to_image_keypoints(
    positions: NDArray[np.float32], scores: NDArray[np.float32], window: CropWindow
) -> tuple[Keypoint, ...]:
    """Map model-input positions back to image pixels."""
    return tuple(
        Keypoint(
            x=window.x0 + float(px) / INPUT_W * window.width,
            y=window.y0 + float(py) / INPUT_H * window.height,
            score=float(score),
        )
        for (px, py), score in zip(positions, scores, strict=True)
    )


class RtmposeOnnx:
    """RTMPose (body, 256x192) on an ONNX session.

    Args:
        session: An ONNX session for the RTMPose model.
        input_name: The model's input tensor name.
    """

    def __init__(self, session: OnnxSession, input_name: str) -> None:
        """Store the session and the model's input name."""
        self._session = session
        self._input_name = input_name

    @classmethod
    def load(cls, model_path: Path) -> RtmposeOnnx:
        """Load an RTMPose ONNX file on the CPU (needs the `ml` extra).

        Args:
            model_path: Path to the exported ONNX model.

        Returns:
            A ready estimator.
        """
        import onnxruntime  # noqa: PLC0415 - optional `ml` extra, only needed to load a model

        session = onnxruntime.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        return cls(session, session.get_inputs()[0].name)

    def estimate(self, frame: Frame, box_xyxy: BoxXYXY) -> tuple[Keypoint, ...]:
        """Return 17 keypoints in image pixels for the person inside `box_xyxy`."""
        window = crop_window(box_xyxy)
        outputs = self._session.run(None, {self._input_name: prepare_input(frame, window)})
        positions, scores = decode_simcc(outputs[0], outputs[1])
        return to_image_keypoints(positions, scores, window)


def sample_indices(count: int, stride: int) -> tuple[int, ...]:
    """Pick the track points to run the model on: every `stride`-th, plus the last.

    Args:
        count: Number of points in the track.
        stride: Gap between sampled points, at least 1.

    Returns:
        Ascending indices.

    Raises:
        ValueError: If `stride` is below 1.
    """
    if stride < 1:
        raise ValueError("stride must be at least 1")
    if count == 0:
        return ()
    indices = list(range(0, count, stride))
    if indices[-1] != count - 1:
        indices.append(count - 1)
    return tuple(indices)


def _blend(a: Keypoint, b: Keypoint, weight: float) -> Keypoint:
    """Linear blend of two keypoints; the score is the lower of the two, to stay conservative."""
    return Keypoint(
        x=a.x + (b.x - a.x) * weight,
        y=a.y + (b.y - a.y) * weight,
        score=min(a.score, b.score),
    )


def fill_keypoints(
    points: Sequence[TrackPoint],
    sampled: Mapping[int, tuple[Keypoint, ...]],
    max_gap_frames: int = 15,
) -> tuple[TrackPoint, ...]:
    """Fill keypoints between pose-estimated points by linear interpolation over frame index.

    Points before the first or after the last estimated one, and points between two estimates more
    than `max_gap_frames` apart, keep no keypoints.

    Args:
        points: The track's points in time order.
        sampled: Estimated keypoints, keyed by index into `points`.
        max_gap_frames: Largest frame distance between two estimates that may be bridged.

    Returns:
        The points with keypoints set where known or interpolated.

    Raises:
        ValueError: If two bracketing estimates have different keypoint counts.
    """
    ordered = sorted(sampled)
    result = []
    for i, point in enumerate(points):
        if i in sampled:
            result.append(point.model_copy(update={"keypoints": sampled[i]}))
            continue
        slot = bisect_left(ordered, i)
        if slot == 0 or slot == len(ordered):
            result.append(point)
            continue
        before, after = ordered[slot - 1], ordered[slot]
        span = points[after].frame_idx - points[before].frame_idx
        if span > max_gap_frames or span <= 0:
            result.append(point)
            continue
        first, last = sampled[before], sampled[after]
        if len(first) != len(last):
            raise ValueError("bracketing estimates must have the same keypoint count")
        weight = (point.frame_idx - points[before].frame_idx) / span
        blended = tuple(_blend(a, b, weight) for a, b in zip(first, last, strict=True))
        result.append(point.model_copy(update={"keypoints": blended}))
    return tuple(result)


def estimate_track(
    track: Track,
    frame_at: Callable[[int], Frame | None],
    estimator: PoseEstimator,
    stride: int = 3,
    max_gap_frames: int = 15,
) -> Track:
    """Return `track` with keypoints on every point that can be sampled or interpolated.

    Args:
        track: A person track.
        frame_at: Returns the RGB frame for a frame index, or None if it is no longer available.
        estimator: The pose model.
        stride: Run the model on every `stride`-th point.
        max_gap_frames: Largest frame gap that interpolation may bridge.

    Returns:
        A new track; the input is unchanged.
    """
    sampled = {}
    for i in sample_indices(len(track.points), stride):
        frame = frame_at(track.points[i].frame_idx)
        if frame is not None:
            sampled[i] = estimator.estimate(frame, track.points[i].box_xyxy)
    filled = fill_keypoints(track.points, sampled, max_gap_frames)
    return track.model_copy(update={"points": filled})


__all__ = [
    "CropWindow",
    "PoseEstimator",
    "RtmposeOnnx",
    "crop_window",
    "decode_simcc",
    "estimate_track",
    "fill_keypoints",
    "prepare_input",
    "sample_indices",
    "to_image_keypoints",
]
