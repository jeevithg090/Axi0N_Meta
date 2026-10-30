"""Core value types shared by every Rulecam module (task book §4.1)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

BoxXYXY = tuple[float, float, float, float]
PointXY = tuple[float, float]


class Frozen(BaseModel):
    """Immutable, strict base model for all contracts."""

    model_config = CONTRACT_MODEL_CONFIG


class Provenance(StrEnum):
    """Where a number or clip came from. Never mixed inside one aggregate (SA §9)."""

    RECORDED = "recorded"
    UPLOAD = "upload"
    PUBLIC = "public"
    SIMULATED = "simulated"


class PrivacyMode(StrEnum):
    """Which pixels may leave a module (MP §7.5)."""

    STANDARD = "standard"
    ANONYMOUS = "anonymous"
    STRICT = "strict"


class Severity(StrEnum):
    """Rule severity; HIGH rules act first and verify after (MP §3.4)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Chunk(Frozen):
    """A rolling H.264 (faststart) video chunk from one camera."""

    chunk_id: str
    camera_id: str
    path: str
    start_ts: datetime
    fps: float = Field(gt=0)
    n_frames: int = Field(gt=0)
    width: int
    height: int
    provenance: Provenance = Provenance.RECORDED


class Mask(Frozen):
    """Segmentation mask as COCO RLE plus its box."""

    rle: str
    box_xyxy: BoxXYXY
    confidence: float | None = None


class Detection(Frozen):
    """One object instance in one frame for one concept (SAM output)."""

    camera_id: str
    frame_idx: int
    ts: datetime
    concept: str
    object_id: int
    mask: Mask


class Keypoint(Frozen):
    """One pose keypoint in image pixels with its confidence score."""

    x: float
    y: float
    score: float


class TrackPoint(Frozen):
    """One sample of a track: box, foot point, optional metric position and pose."""

    ts: datetime
    frame_idx: int
    box_xyxy: BoxXYXY
    foot_px: PointXY
    foot_m: PointXY | None = None  # ground-plane metres, if calibrated
    confidence: float | None = None
    keypoints: tuple[Keypoint, ...] | None = None  # COCO-17 order when pose is on


class Track(Frozen):
    """A tracked object.

    `track_id` is opaque and MUST NOT be used as an analytics grouping key (rule R13).
    """

    track_id: str
    camera_id: str
    concept: str
    points: tuple[TrackPoint, ...]


class Calibration(Frozen):
    """Ground-plane homography for one camera (image px -> ground metres)."""

    camera_id: str
    homography: tuple[tuple[float, float, float], ...]  # 3x3, image px -> ground metres
    method: Literal["clicks4", "reference_object"]
    reference: str | None = None  # e.g. "pallet_48x40in", "floor_square_1m"
    err_m: float = Field(ge=0)  # 1-sigma ground error estimate


class Zone(Frozen):
    """A named region: an image polygon, or a metric buffer around a concept."""

    zone_id: str
    camera_id: str
    kind: Literal["polygon", "buffer"]
    polygon_px: tuple[PointXY, ...] | None = None
    buffer_of_concept: str | None = None
    buffer_m: float | None = None


class Measurement(Frozen):
    """A measured value with an optional interval and its unit."""

    value: float
    lo: float | None = None
    hi: float | None = None
    unit: str
