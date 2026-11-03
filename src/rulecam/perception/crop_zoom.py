"""Crop-zoom SAM 3.1: look at a person's head, hands or body up close (add-on 3.3).

Small PPE is hard to see in a full frame: a head is about 30 px tall in a 720p shop camera, so the
hat in one live run was rated "not observable" (0.39) and Glimmer suppressed 8 of 20 kitchen
candidates as hair nets. Ready-made PPE detectors only know hard hats and vests. SAM 3.1 already
detects any phrase; it only needs to see the region at a useful size. This module cuts that
region out of the frame around each person and asks SAM 3.1 for the item there.

- `person_crops` derives the crops from a person's box, and from their pose when known: the head
  (with room above it for a hat or hair net; from the face keypoints when known, else the top of
  the box), the hands (a square around each known wrist, else the arm band of the box) and the
  whole body (the box, padded). A `without` predicate's region maps to one of them
  (`WITHOUT_REGIONS`).
- `CropSegmenter` runs SAM 3.1 on crops of one frame. `WorkerCrops` sends the frame once to the
  GPU pod's `POST /segment_crops` (`crop_worker.CropEngine`, no extra weights); `LocalCrops`
  cuts the crops here and sends each to an image segmenter (hosted SAM 3.1), for sites without
  a GPU pod; `FallbackCrops` tries one, then the other. Every answer's boxes are frame pixels.

Two callers use it: the planner's probe (`planner.steps.probe`), where a worn item Spark names is
looked for on enlarged crops of each person in a frame where the full frame missed it, and the
verifier's Tier 0 (`verify.tier0`), whose findings go to Glimmer as context.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Final, Literal, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import Field, ValidationError

from rulecam.clients.errors import ModelClientError
from rulecam.contracts.core import BoxXYXY, Detection, Frozen, Keypoint
from rulecam.log import get_logger
from rulecam.perception.errors import SamResponseError, SamRuntimeError

_log = get_logger(__name__)

CropRegion = Literal["head", "hands", "body"]
CROP_REGIONS: Final[tuple[CropRegion, ...]] = ("head", "hands", "body")
WITHOUT_REGIONS: Final[dict[str, CropRegion]] = {
    "top_25pct": "head",
    "hands": "hands",
    "full": "body",
}
HEAD_SIDE: Final = 0.34  # of the person's box height: a head with a hat or hair net on it
HEAD_CENTRE: Final = 0.12  # from the top of the box, as a share of its height
HAND_SIDE: Final = 0.3  # of the box height, a square around a known wrist
ARMS_BAND: Final = (0.3, 0.85)  # rows of the box where hands hang or work, without pose
SIDE_MARGIN: Final = 0.2  # of the box width on each side, for arms held out
BODY_PAD: Final = 0.1
MIN_SIDE_PX: Final = 48.0
MIN_KEYPOINT_SCORE: Final = 0.3
HEAD_KEYPOINTS: Final = range(5)  # COCO-17: nose, eyes, ears (only located, never identified)
WRISTS: Final = (9, 10)
JPEG_QUALITY: Final = 95

Frame = NDArray[np.uint8]


class ZoomCrop(Frozen):
    """One crop around one person: which person, which region, where in the frame."""

    person_idx: int = Field(ge=0)
    region: CropRegion
    box_xyxy: BoxXYXY


class CropHit(Frozen):
    """One detection on one crop, in frame pixels."""

    crop_idx: int = Field(ge=0)
    concept: str
    box_xyxy: BoxXYXY
    confidence: float | None = None


class CropsResponse(Frozen):
    """The GPU pod's answer to one `/segment_crops` call."""

    objects: tuple[CropHit, ...] = ()
    crops_processed: int = Field(ge=0)
    gpu_seconds: float = Field(ge=0)
    model_version: str


def _square(cx: float, cy: float, side: float, width: int, height: int) -> BoxXYXY | None:
    """A square around `(cx, cy)` clipped to the frame, or `None` when nothing is left."""
    half = max(side, MIN_SIDE_PX) / 2
    box = (max(0.0, cx - half), max(0.0, cy - half), min(width, cx + half), min(height, cy + half))
    return box if box[2] - box[0] >= 1 and box[3] - box[1] >= 1 else None


def _clip(box: BoxXYXY, width: int, height: int) -> BoxXYXY | None:
    x0, y0, x1, y1 = box
    clipped = (max(0.0, x0), max(0.0, y0), min(float(width), x1), min(float(height), y1))
    return clipped if clipped[2] - clipped[0] >= 1 and clipped[3] - clipped[1] >= 1 else None


def _known(keypoints: Sequence[Keypoint] | None, indices: Sequence[int]) -> list[Keypoint]:
    found = keypoints or ()
    return [found[k] for k in indices if k < len(found) and found[k].score >= MIN_KEYPOINT_SCORE]


def head_crop(
    box: BoxXYXY, width: int, height: int, keypoints: Sequence[Keypoint] | None = None
) -> BoxXYXY | None:
    """The head crop: centred on the face keypoints when known, else on the box's top.

    The square is `HEAD_SIDE` of the person's height (at least `MIN_SIDE_PX`), so a hat or hair
    net above the head is inside it.
    """
    x0, y0, x1, y1 = box
    person_h = y1 - y0
    side = HEAD_SIDE * person_h
    head = _known(keypoints, HEAD_KEYPOINTS)
    if head:
        cx = float(np.mean([k.x for k in head]))
        cy = float(np.mean([k.y for k in head])) - 0.03 * person_h
        spread = max(max(k.x for k in head) - min(k.x for k in head), 1.0)
        return _square(cx, cy, max(side, 3.0 * spread), width, height)
    return _square((x0 + x1) / 2, y0 + HEAD_CENTRE * person_h, side, width, height)


def hand_crops(
    box: BoxXYXY, width: int, height: int, keypoints: Sequence[Keypoint] | None = None
) -> list[BoxXYXY]:
    """The hand crops: a square around each known wrist, else the arm band of the box."""
    x0, y0, x1, y1 = box
    person_h, person_w = y1 - y0, x1 - x0
    wrists = _known(keypoints, WRISTS)
    if wrists:
        squares = [_square(k.x, k.y, HAND_SIDE * person_h, width, height) for k in wrists]
        return [s for s in squares if s is not None]
    top, bottom = ARMS_BAND
    band = (
        x0 - SIDE_MARGIN * person_w,
        y0 + top * person_h,
        x1 + SIDE_MARGIN * person_w,
        y0 + bottom * person_h,
    )
    clipped = _clip(band, width, height)
    return [clipped] if clipped is not None else []


def body_crop(box: BoxXYXY, width: int, height: int) -> BoxXYXY | None:
    """The whole person, padded by `BODY_PAD` on every side."""
    x0, y0, x1, y1 = box
    pad_x, pad_y = BODY_PAD * (x1 - x0), BODY_PAD * (y1 - y0)
    return _clip((x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y), width, height)


def person_crops(
    box: BoxXYXY,
    *,
    regions: Sequence[CropRegion],
    width: int,
    height: int,
    keypoints: Sequence[Keypoint] | None = None,
    person_idx: int = 0,
) -> list[ZoomCrop]:
    """The crops of one person for the given regions, clipped to the frame.

    Args:
        box: The person's box in frame pixels.
        regions: Which regions to crop (each once, in the given order).
        width: Frame width in pixels.
        height: Frame height in pixels.
        keypoints: The person's COCO-17 keypoints in frame pixels, when pose is known.
        person_idx: Recorded on each crop, so hits can be traced back to the person.

    Returns:
        The crops; a region that falls outside the frame gives none.
    """
    if not (box[2] > box[0] and box[3] > box[1]):
        return []
    crops: list[ZoomCrop] = []
    for region in dict.fromkeys(regions):
        if region == "head":
            boxes = [b] if (b := head_crop(box, width, height, keypoints)) else []
        elif region == "hands":
            boxes = hand_crops(box, width, height, keypoints)
        else:
            boxes = [b] if (b := body_crop(box, width, height)) else []
        crops.extend(ZoomCrop(person_idx=person_idx, region=region, box_xyxy=b) for b in boxes)
    return crops


def encode_jpeg(frame: Frame) -> bytes:
    """A BGR frame as JPEG bytes (quality `JPEG_QUALITY`).

    Raises:
        ValueError: If OpenCV cannot encode it.
    """
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        msg = "frame could not be encoded as JPEG"
        raise ValueError(msg)
    return bytes(data.tobytes())


class CropSegmenter(Protocol):
    """Runs SAM 3.1 on crops of one frame."""

    def segment_crops(
        self,
        image: bytes,
        crops: Sequence[BoxXYXY],
        concepts: Sequence[str],
        *,
        camera_id: str,
    ) -> list[CropHit]:
        """Detect every concept on every crop; boxes in frame pixels, `crop_idx` into `crops`."""
        ...


class SamCropWorker(Protocol):
    """The GPU pod's `/segment_crops` (`sam_runtime.HttpSamWorker.segment_crops`)."""

    def segment_crops(self, image: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Return the server's answer dict."""
        ...


def parse_crops_response(raw: object, n_crops: int, concepts: Sequence[str]) -> CropsResponse:
    """Validate the GPU pod's answer.

    Raises:
        SamResponseError: Malformed, or naming a crop that was not sent or a concept not asked.
    """
    try:
        response = CropsResponse.model_validate(raw)
    except ValidationError as exc:
        msg = "malformed SAM crops response"
        raise SamResponseError(msg) from exc
    asked = set(concepts)
    for hit in response.objects:
        if hit.crop_idx >= n_crops or hit.concept not in asked:
            msg = f"SAM crops response names crop {hit.crop_idx} / concept {hit.concept!r}"
            raise SamResponseError(msg)
    return response


class WorkerCrops:
    """`CropSegmenter` on the GPU pod's `POST /segment_crops`: one frame upload per call."""

    def __init__(self, worker: SamCropWorker) -> None:
        """Wrap the pod's client (`HttpSamWorker`)."""
        self._worker = worker

    def segment_crops(
        self,
        image: bytes,
        crops: Sequence[BoxXYXY],
        concepts: Sequence[str],
        *,
        camera_id: str,
    ) -> list[CropHit]:
        """Run the crops on the GPU pod.

        Raises:
            SamRuntimeError: The pod could not serve the call or answered malformed data.
        """
        if not crops or not concepts:
            return []
        request = {
            "concepts": list(dict.fromkeys(concepts)),
            "crops": [list(box) for box in crops],
            "include_confidence": True,
        }
        response = parse_crops_response(
            self._worker.segment_crops(image, request), len(crops), concepts
        )
        _log.info(
            "sam_crops",
            camera_id=camera_id,
            crops=response.crops_processed,
            hits=len(response.objects),
            gpu_s=round(response.gpu_seconds, 3),
        )
        return list(response.objects)


class BytesSegmenter(Protocol):
    """SAM 3.1 on one encoded image (`HostedSam31.segment_image_bytes`)."""

    def segment_image_bytes(
        self,
        image: bytes,
        concept: str,
        *,
        camera_id: str = ...,
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Return the concept's detections in the image."""
        ...


class LocalCrops:
    """`CropSegmenter` that cuts the crops here and sends each one to an image segmenter.

    One request per crop and concept (hosted SAM 3.1 bills each as one image), so callers keep
    crops and concepts few.
    """

    def __init__(self, segmenter: BytesSegmenter) -> None:
        """Wrap an image segmenter (hosted SAM 3.1)."""
        self._segmenter = segmenter

    def segment_crops(
        self,
        image: bytes,
        crops: Sequence[BoxXYXY],
        concepts: Sequence[str],
        *,
        camera_id: str,
    ) -> list[CropHit]:
        """Cut, encode and segment each crop.

        Raises:
            ValueError: If the image cannot be decoded.
        """
        pixels = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if pixels is None:
            msg = "image bytes could not be decoded"
            raise ValueError(msg)
        height, width = pixels.shape[:2]
        hits: list[CropHit] = []
        for crop_idx, box in enumerate(crops):
            x0, y0 = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
            x1, y1 = min(width, math.ceil(box[2])), min(height, math.ceil(box[3]))
            if x1 - x0 < 2 or y1 - y0 < 2:  # noqa: PLR2004 - nothing to look at
                continue
            crop = encode_jpeg(np.ascontiguousarray(pixels[y0:y1, x0:x1]))
            for concept in dict.fromkeys(concepts):
                for detection in self._segmenter.segment_image_bytes(
                    crop, concept, camera_id=camera_id, include_confidence=True
                ):
                    bx0, by0, bx1, by1 = detection.mask.box_xyxy
                    hits.append(
                        CropHit(
                            crop_idx=crop_idx,
                            concept=concept,
                            box_xyxy=(bx0 + x0, by0 + y0, bx1 + x0, by1 + y0),
                            confidence=detection.mask.confidence,
                        )
                    )
        return hits


class FallbackCrops:
    """Tries the GPU pod first and an image segmenter when the pod cannot serve the call."""

    def __init__(self, primary: CropSegmenter, fallback: CropSegmenter) -> None:
        """Order the two segmenters."""
        self._primary = primary
        self._fallback = fallback

    def segment_crops(
        self,
        image: bytes,
        crops: Sequence[BoxXYXY],
        concepts: Sequence[str],
        *,
        camera_id: str,
    ) -> list[CropHit]:
        """The primary's answer, or the fallback's when the primary fails."""
        try:
            return self._primary.segment_crops(image, crops, concepts, camera_id=camera_id)
        except (SamRuntimeError, ModelClientError) as exc:
            _log.warning("sam_crops_fallback", camera_id=camera_id, error=str(exc))
            return self._fallback.segment_crops(image, crops, concepts, camera_id=camera_id)


__all__ = [
    "CROP_REGIONS",
    "WITHOUT_REGIONS",
    "BytesSegmenter",
    "CropHit",
    "CropRegion",
    "CropSegmenter",
    "CropsResponse",
    "FallbackCrops",
    "LocalCrops",
    "SamCropWorker",
    "WorkerCrops",
    "ZoomCrop",
    "body_crop",
    "encode_jpeg",
    "hand_crops",
    "head_crop",
    "parse_crops_response",
    "person_crops",
]
