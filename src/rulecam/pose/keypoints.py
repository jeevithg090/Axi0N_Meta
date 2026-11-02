"""Pose estimation for tracked people (task P4-04).

`RtmposeOnnx` is the CPU path (RTMPose, Apache-2.0). `PoseEstimator` is the seam where a Sapiens
worker on a GPU can plug in. Poses are estimated every few frames and filled between by linear
interpolation. Only person tracks should be passed in: this produces joints, never identity.

The pinned checkpoint is OpenMMLab's RTMPose-l body7 (`RTMPOSE_BODY7_NAME`, COCO val AP 76.7),
exported to ONNX by OpenMMLab (`RTMPOSE_BODY7_URL`, a zip whose `end2end.onnx` has the sha256
`RTMPOSE_BODY7_ONNX_SHA256`). Its preprocessing (192 x 256 input, padding 1.25, ImageNet mean and
std in RGB) and SimCC split ratio 2.0 are what this module implements. It runs at about 20 ms per
person crop on a laptop CPU. `scripts/fetch_rtmpose.sh` downloads and checks it;
`load_pose_estimator` loads it for the runtime (`RULECAM_POSE_ONNX`), or returns `None`.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from rulecam.contracts import Keypoint, Track, TrackPoint
from rulecam.contracts.core import BoxXYXY
from rulecam.log import get_logger

_log = get_logger(__name__)

INPUT_W, INPUT_H = 192, 256
SIMCC_SPLIT_RATIO = 2.0
BOX_PADDING = 1.25
MEAN = np.array((123.675, 116.28, 103.53), dtype=np.float32)
STD = np.array((58.395, 57.12, 57.375), dtype=np.float32)
RTMPOSE_BODY7_NAME = "rtmpose-l_simcc-body7_pt-body7_420e-256x192-4dba18fc_20230504"
RTMPOSE_BODY7_URL = (
    f"https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/{RTMPOSE_BODY7_NAME}.zip"
)
RTMPOSE_BODY7_ZIP_SHA256 = "121f42bf3f3e28f2cf254696ae649e58459513f65d2b4580959b688c0b2c8302"
RTMPOSE_BODY7_ONNX_SHA256 = "cff059fd58a2c0d5fabaddcd66a96abcfb327563bcb0149ea59c9de4a8990fe2"

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
        frame: A BGR image (OpenCV order, like every frame in the pipeline), height x width x 3.
            It is converted to RGB for the model. Areas outside the frame become black.
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
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    normalised = (rgb.astype(np.float32) - MEAN) / STD
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
    def load(cls, model_path: Path, *, sha256: str) -> RtmposeOnnx:
        """Load an RTMPose ONNX file on the CPU (needs the `ml` extra).

        The checkpoint must be pinned: its sha256 is required and checked, so the weights that ran
        are the weights whose licence was reviewed (see `WHAT-WE-BUILT.md`).

        Args:
            model_path: Path to the exported ONNX model.
            sha256: The file's expected sha256, as hex.

        Returns:
            A ready estimator.

        Raises:
            ValueError: If the file's sha256 is not `sha256`.
        """
        actual = hashlib.sha256(model_path.read_bytes()).hexdigest()
        if actual != sha256.lower():
            raise ValueError(f"{model_path.name} has sha256 {actual}, expected {sha256.lower()}")
        import onnxruntime  # noqa: PLC0415 - optional `ml` extra, only needed to load a model

        session = onnxruntime.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        return cls(session, session.get_inputs()[0].name)

    def estimate(self, frame: Frame, box_xyxy: BoxXYXY) -> tuple[Keypoint, ...]:
        """Return 17 keypoints in image pixels for the person inside `box_xyxy`."""
        window = crop_window(box_xyxy)
        outputs = self._session.run(None, {self._input_name: prepare_input(frame, window)})
        positions, scores = decode_simcc(outputs[0], outputs[1])
        return to_image_keypoints(positions, scores, window)


def load_pose_estimator(
    model_path: Path | None, *, sha256: str = RTMPOSE_BODY7_ONNX_SHA256
) -> RtmposeOnnx | None:
    """Load the runtime's pose model, or `None` (logged) when it cannot run.

    Pose is optional evidence: without it, strips draw a figure fitted to the person's box, so a
    missing file, a missing `onnxruntime` or a file with another hash never stops the runtime.

    Args:
        model_path: The RTMPose ONNX file (`RULECAM_POSE_ONNX`), or `None` for no pose.
        sha256: The file's pinned sha256 (`RULECAM_POSE_SHA256`).

    Returns:
        The estimator, or `None`.
    """
    if model_path is None:
        return None
    if not model_path.is_file():
        _log.warning("pose_model_missing", path=str(model_path))
        return None
    try:
        estimator = RtmposeOnnx.load(model_path, sha256=sha256)
    except Exception as exc:  # a missing extra, a wrong hash or a bad file: run without pose
        _log.warning("pose_model_unavailable", path=str(model_path), error=str(exc))
        return None
    _log.info("pose_model_loaded", path=str(model_path))
    return estimator


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
    max_gap_s: float = 1.5,
) -> tuple[TrackPoint, ...]:
    """Fill keypoints between pose-estimated points by linear interpolation over time.

    Weights come from `ts`, never `frame_idx`: frame indices restart in every 3-second chunk, but
    a track window spans chunks. The weight is kept between 0 and 1, so a value is never
    extrapolated. Points before the first or after the last estimated one, and points between two
    estimates more than `max_gap_s` apart, keep no keypoints.

    Args:
        points: The track's points in time order.
        sampled: Estimated keypoints, keyed by index into `points`.
        max_gap_s: Largest time between two estimates that may be bridged, in seconds.

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
        span = (points[after].ts - points[before].ts).total_seconds()
        if span > max_gap_s or span <= 0:
            result.append(point)
            continue
        first, last = sampled[before], sampled[after]
        if len(first) != len(last):
            raise ValueError("bracketing estimates must have the same keypoint count")
        weight = (point.ts - points[before].ts).total_seconds() / span
        weight = min(1.0, max(0.0, weight))
        blended = tuple(_blend(a, b, weight) for a, b in zip(first, last, strict=True))
        result.append(point.model_copy(update={"keypoints": blended}))
    return tuple(result)


def estimate_track(
    track: Track,
    frame_at: Callable[[datetime], Frame | None],
    estimator: PoseEstimator,
    stride: int = 3,
    max_gap_s: float = 1.5,
) -> Track:
    """Return `track` with keypoints on every point that can be sampled or interpolated.

    Args:
        track: A person track.
        frame_at: Returns the BGR frame at a timestamp, or None if it is no longer available.
            Frames are looked up by `ts`, not `frame_idx`, which restarts in every chunk.
        estimator: The pose model.
        stride: Run the model on every `stride`-th point.
        max_gap_s: Largest time gap, in seconds, that interpolation may bridge.

    Returns:
        A new track; the input is unchanged.
    """
    sampled = {}
    for i in sample_indices(len(track.points), stride):
        frame = frame_at(track.points[i].ts)
        if frame is not None:
            sampled[i] = estimator.estimate(frame, track.points[i].box_xyxy)
    filled = fill_keypoints(track.points, sampled, max_gap_s)
    return track.model_copy(update={"points": filled})


__all__ = [
    "RTMPOSE_BODY7_NAME",
    "RTMPOSE_BODY7_ONNX_SHA256",
    "RTMPOSE_BODY7_URL",
    "RTMPOSE_BODY7_ZIP_SHA256",
    "CropWindow",
    "PoseEstimator",
    "RtmposeOnnx",
    "crop_window",
    "decode_simcc",
    "estimate_track",
    "fill_keypoints",
    "load_pose_estimator",
    "prepare_input",
    "sample_indices",
    "to_image_keypoints",
]
