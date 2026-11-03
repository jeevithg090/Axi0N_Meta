"""Crop-zoom SAM 3.1 on the GPU pod: small things on enlarged crops (add-on 3.3, `/segment_crops`).

A hat, a hair net or a glove is a few dozen pixels in a full 1280 x 720 frame, and SAM 3.1 resizes
every image to 1008 x 1008 before its encoder, so most of its input is floor and shelving. Cut
out a person's head or hands first and the same detector sees that region at several times the
resolution, for one encoder pass per crop (about 0.1 s on an RTX 4090) and no extra weights or
VRAM: `CropEngine` runs the detector of the already loaded SAM 3.1 model, like
`sam_worker.Sam31ImageEngine`, through its own `Sam3Processor` so its confidence threshold
(`SAM_CROP_THRESH`, default 0.3) can sit below the keyframes' (`SAM_IMAGE_THRESH`): the caller
gets every score and decides, and a small item seen up close scores lower than a person.

The client sends one frame and the crop boxes (`perception.crop_zoom` derives them from person
boxes and pose), not the crops, so a frame crosses the network once whatever the number of
crops. Boxes in the answer are in the frame's pixels.

Like `sam_worker`, this module runs inside the GPU venv: it imports numpy, `sam_worker` and,
lazily, OpenCV and `sam3`; never pydantic, structlog or httpx. The request is
`{"concepts": [...], "crops": [[x0, y0, x1, y1], ...], "include_confidence": bool}`; the answer
`{"objects": [{"crop_idx", "concept", "box_xyxy", "confidence"}], "crops_processed",
"gpu_seconds", "model_version"}`.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Any

import numpy as np

from rulecam.perception.sam_worker import (
    MODEL_VERSION,
    GpuMemory,
    ImagePredictor,
    NoGpuMemory,
    Sam3ProcessorPredictor,
    Sam31Engine,
    decode_jpeg,
)

CROP_MODEL_VERSION = f"{MODEL_VERSION}#detector-crops"
CROP_THRESH_ENV = "SAM_CROP_THRESH"
DEFAULT_CROP_THRESH = 0.3
MAX_CROPS = 48
MAX_CROP_CONCEPTS = 8
MIN_CROP_PX = 8  # a crop narrower or shorter than this (after clipping) is skipped

_log = logging.getLogger("rulecam.crop_worker")


def crop_pixels(
    image: np.ndarray, box: Sequence[float]
) -> tuple[np.ndarray, tuple[int, int]] | None:
    """Cut a box out of an image, clipped to it.

    Args:
        image: RGB pixels (H, W, 3).
        box: `(x0, y0, x1, y1)` in image pixels.

    Returns:
        The crop's pixels and its top-left corner, or `None` when less than `MIN_CROP_PX` of the
        box lies inside the image on either axis.
    """
    height, width = image.shape[:2]
    x0, y0 = max(0, int(np.floor(box[0]))), max(0, int(np.floor(box[1])))
    x1, y1 = min(width, int(np.ceil(box[2]))), min(height, int(np.ceil(box[3])))
    if x1 - x0 < MIN_CROP_PX or y1 - y0 < MIN_CROP_PX:
        return None
    return np.ascontiguousarray(image[y0:y1, x0:x1]), (x0, y0)


def crop_objects(
    crop_idx: int,
    concept: str,
    found: tuple[np.ndarray, np.ndarray, np.ndarray],
    origin: tuple[int, int],
    size: tuple[int, int],
    *,
    include_confidence: bool,
) -> list[dict[str, Any]]:
    """One crop's detections of one concept as wire objects, boxes in frame pixels.

    Args:
        crop_idx: The crop's place in the request.
        concept: The concept.
        found: `(boxes, scores, masks)` from `ImagePredictor.detect`, in crop pixels.
        origin: The crop's top-left corner in the frame.
        size: The crop's `(width, height)`; boxes are clipped to it.
        include_confidence: Whether to report the scores.

    Returns:
        One dict per detection, best score first.
    """
    boxes, scores, _ = found
    ox, oy = origin
    width, height = size
    objects = []
    for i in np.argsort(-scores, kind="stable"):
        x0, y0, x1, y1 = (float(v) for v in boxes[i])
        x0, x1 = (min(max(v, 0.0), float(width)) + ox for v in (x0, x1))
        y0, y1 = (min(max(v, 0.0), float(height)) + oy for v in (y0, y1))
        objects.append(
            {
                "crop_idx": crop_idx,
                "concept": concept,
                "box_xyxy": [x0, y0, x1, y1],
                "confidence": float(scores[i]) if include_confidence else None,
            }
        )
    return objects


def check_crops_request(request: dict[str, Any]) -> tuple[list[str], list[list[float]]]:
    """Validate a `/segment_crops` request's concepts and crop boxes.

    Raises:
        ValueError: No or too many concepts or crops, an empty concept, or a box that is not four
            finite numbers with `x1 > x0` and `y1 > y0`.
    """
    concepts, crops = request.get("concepts"), request.get("crops")
    if not isinstance(concepts, list) or not 0 < len(concepts) <= MAX_CROP_CONCEPTS:
        msg = f"`concepts` must list 1 to {MAX_CROP_CONCEPTS} noun phrases"
        raise ValueError(msg)
    if not all(isinstance(c, str) and c.strip() for c in concepts):
        msg = "every concept must be a non-empty string"
        raise ValueError(msg)
    if not isinstance(crops, list) or not 0 < len(crops) <= MAX_CROPS:
        msg = f"`crops` must list 1 to {MAX_CROPS} boxes"
        raise ValueError(msg)
    boxes: list[list[float]] = []
    for box in crops:
        if not isinstance(box, list) or len(box) != 4:  # noqa: PLR2004 - x0, y0, x1, y1
            msg = "every crop must be [x0, y0, x1, y1]"
            raise ValueError(msg)
        if not all(isinstance(v, int | float) and np.isfinite(v) for v in box):
            msg = "crop coordinates must be finite numbers"
            raise ValueError(msg)
        if not (box[2] > box[0] and box[3] > box[1]):
            msg = "every crop needs x1 > x0 and y1 > y0"
            raise ValueError(msg)
        boxes.append([float(v) for v in box])
    return [str(c) for c in concepts], boxes


class CropEngine:
    """SAM 3.1 detection on crops of one frame; serves `POST /segment_crops`.

    After every call, also a failed one, GPU memory is released, as after a keyframe batch.
    """

    def __init__(
        self,
        predictor: ImagePredictor,
        inference_context: Callable[[], AbstractContextManager[object]] = nullcontext,
        *,
        memory: GpuMemory | None = None,
        decode: Callable[[bytes], np.ndarray] = decode_jpeg,
    ) -> None:
        """Wrap a loaded image predictor.

        Args:
            predictor: The SAM 3.1 detector behind an `ImagePredictor`, or a fake in tests.
            inference_context: Entered around every call (bfloat16 autocast on a GPU).
            memory: Frees GPU memory after each call (defaults to `NoGpuMemory`).
            decode: Decodes an image's bytes into RGB pixels.
        """
        self._predictor = predictor
        self._inference_context = inference_context
        self._memory: GpuMemory = memory or NoGpuMemory()
        self._decode = decode
        self._calls = 0
        self._crops = 0

    @classmethod
    def from_video_engine(cls, engine: Sam31Engine) -> CropEngine:  # pragma: no cover - GPU
        """Serve crops with the detector of an already loaded video engine (needs a GPU)."""
        from sam3.model.sam3_image_processor import (  # type: ignore[import-not-found]  # noqa: PLC0415
            Sam3Processor,
        )

        threshold = float(os.environ.get(CROP_THRESH_ENV) or DEFAULT_CROP_THRESH)
        video_predictor: Any = engine.predictor
        processor = Sam3Processor(video_predictor.model.detector, confidence_threshold=threshold)
        _log.info("sam_crop_engine_loaded threshold=%s", threshold)
        return cls(
            Sam3ProcessorPredictor(processor),
            inference_context=engine.inference_context,
            memory=engine.memory,
        )

    def segment_crops(self, image: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Detect every concept of `request` on every crop of `image`.

        Args:
            image: The frame as JPEG or PNG bytes.
            request: `{"concepts", "crops", "include_confidence"}` (see the module docstring).

        Returns:
            The answer dict, boxes in frame pixels.

        Raises:
            ValueError: If the request is malformed or the image cannot be decoded.
        """
        started = time.perf_counter()
        concepts, boxes = check_crops_request(request)
        confidence = bool(request.get("include_confidence", True))
        pixels = self._decode(image)
        objects: list[dict[str, Any]] = []
        processed = 0
        try:
            with self._inference_context():
                for crop_idx, box in enumerate(boxes):
                    cut = crop_pixels(pixels, box)
                    if cut is None:
                        continue
                    crop, origin = cut
                    processed += 1
                    state = self._predictor.set_image(crop)
                    size = (crop.shape[1], crop.shape[0])
                    for concept in concepts:
                        found = self._predictor.detect(state, concept)
                        objects.extend(
                            crop_objects(
                                crop_idx,
                                concept,
                                found,
                                origin,
                                size,
                                include_confidence=confidence,
                            )
                        )
        finally:
            self._calls += 1
            self._crops += processed
            self._memory.release()
        return {
            "objects": objects,
            "crops_processed": processed,
            "gpu_seconds": time.perf_counter() - started,
            "model_version": CROP_MODEL_VERSION,
        }

    def memory_stats(self) -> dict[str, Any]:
        """The crop engine's counters for `/health`."""
        return {"calls": self._calls, "crops": self._crops}
