"""GPU side of the self-hosted SAM 3.1 runtime: the Modal app `rulecam-sam` (task P1-03).

`Sam31Engine` runs Meta's open-weights SAM 3.1 (`facebook/sam3.1`, Object Multiplex) through the
`sam3` package's predictor API (`handle_request` / `handle_stream_request`). The checkpoint has no
Hugging Face Transformers integration, so the GPU image installs `sam3` from GitHub at a pinned
commit. `sam3` needs numpy < 2, which is why it lives only in the GPU image and never in the
repo's lock. The gated checkpoint is downloaded inside the container with the `HF_TOKEN` taken from
`Settings` at deploy time.

GPU memory: every session is followed by `gc.collect()`, `torch.clear_autocast_cache()` and
`torch.cuda.empty_cache()` (tens of milliseconds next to a ~3.5 s session). Upstream
`close_session` clears the session dicts but only empties the CUDA cache above 80 % device use
(`_CLEAR_CACHE_THRESHOLD` in `sam3/model/sam3_base_predictor.py`), and propagation keeps every
frame's video-resolution masks in `inference_state["cached_frame_outputs"]` unless the request
sets `evict_cached_frame_outputs`. A memory guard (`SAM_MEMORY_GUARD_GB`, half the device by
default) closes any session the predictor still holds and frees the cache again when allocated
memory stays high after a session; `memory_stats()` feeds the server's `/health`.

No downscaling: SAM 3.1 resizes every frame to a fixed 1008 x 1008 input before the backbone
(`load_video_frames_from_video_file_using_cv2` in `sam3/model/io_utils.py`, `image_size=1008` in
`build_sam3_multiplex_video_predictor`), so shrinking 1280 x 720 frames first saves no encoder
work and would only cost mask resolution.

Image mode (`Sam31ImageEngine`, the runtime's keyframes) runs the *detector* of the same loaded
SAM 3.1 model (`predictor.model.detector`, a `Sam3MultiplexDetector`, which is a `Sam3Image`)
through `sam3`'s own `Sam3Processor`: one image encoder pass per keyframe, then one
text-conditioned detection per concept on it. It shares the weights already on the GPU (no second
checkpoint) and keeps no session state, so memory stays flat; the video predictor stays loaded
for offline uses. At the pinned commit `build_sam3_image_model` only loads the SAM 3 (not 3.1)
image checkpoint, which is why the engine reuses the SAM 3.1 detector instead.

This module imports with the base dependencies only. It must stay free of pydantic and structlog
because the GPU image ships just numpy and `rulecam.perception.masks` from this repo; `modal`,
`torch` and `sam3` are imported lazily inside the functions that need them.

The request and response dicts are the JSON form of `sam_runtime.SamRequest` and
`sam_runtime.SamResponse`. Deploy once per Modal workspace (needs `uv sync --extra ml`, plus
`HF_TOKEN`, `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in `.env`):

    uv run python -m rulecam.perception.sam_worker
"""

from __future__ import annotations

import functools
import gc
import inspect
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from rulecam.perception.errors import PerceptionError
from rulecam.perception.masks import NO_MASK, decode_rle, encode_rle

MODAL_APP_NAME = "rulecam-sam"
MODAL_CLASS_NAME = "Sam31Worker"
MODEL_VERSION = "facebook/sam3.1@sam3.1_multiplex"
IMAGE_MODEL_VERSION = f"{MODEL_VERSION}#detector"
IMAGE_THRESH_ENV = "SAM_IMAGE_THRESH"
# The floor `Sam3Processor` keeps detections at (its own default is 0.5). Each `/segment_image`
# request then cuts at its `min_confidence`: the runtime's tracker asks for everything down to
# this floor (ByteTrack's low-confidence second pass), every other caller keeps 0.5.
DEFAULT_IMAGE_THRESH = 0.1
LEGACY_MIN_CONFIDENCE = 0.5  # the cut for requests that do not name `min_confidence`
DEFAULT_GPU = "L4"
WARM_CONTAINERS = 1
SCALEDOWN_WINDOW_S = 600
CALL_TIMEOUT_S = 120

SAM3_COMMIT = "f1647e8d0c1d280991f4d1b7d974c2704537fe66"
SAM3_PACKAGE = f"sam3 @ git+https://github.com/facebookresearch/sam3.git@{SAM3_COMMIT}"
TORCH_PACKAGES = ("torch==2.10.0", "torchvision")
TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
# sam3 imports einops, pycocotools and psutil without declaring them; masks -> contracts needs
# pydantic. Keep in step with scripts/sam_pod/setup.sh.
IMAGE_PACKAGES = (
    SAM3_PACKAGE,
    "opencv-python-headless",
    "setuptools<81",
    "pydantic>=2.7",
    "einops",
    "pycocotools",
    "psutil",
)

# GPU memory guard (see the module docstring).
MEMORY_GUARD_ENV = "SAM_MEMORY_GUARD_GB"
DEFAULT_MEMORY_GUARD_FRACTION = 0.5
COMPILE_ENV = "SAM_COMPILE"
GIB = 1024**3
# Optional overrides of the SAM 3.1 tracker's detection settings, read once at load. Upstream
# values (`build_sam3_multiplex_video_predictor`): score_threshold_detection 0.4, new_det_thresh
# 0.65, hotstart_delay 15, hotstart_unmatch_thresh 8, hotstart_dup_thresh 8.
MODEL_OVERRIDE_ENVS: Mapping[str, tuple[str, type[float] | type[int]]] = {
    "SAM_DET_THRESH": ("score_threshold_detection", float),
    "SAM_NEW_DET_THRESH": ("new_det_thresh", float),
    "SAM_HOTSTART_DELAY": ("hotstart_delay", int),
    "SAM_HOTSTART_UNMATCH": ("hotstart_unmatch_thresh", int),
    "SAM_HOTSTART_DUP": ("hotstart_dup_thresh", int),
}
_HOTSTART_LIMITED = ("hotstart_unmatch_thresh", "hotstart_dup_thresh")

WireObject = dict[str, Any]
FrameOutputs = list[tuple[int, Mapping[str, Any]]]

_log = logging.getLogger("rulecam.sam_worker")


class Sam3Predictor(Protocol):
    """The subset of the `sam3` video predictor API the engine uses."""

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run one session request (start, add prompt, close)."""
        ...

    def handle_stream_request(self, request: dict[str, Any]) -> Iterable[dict[str, Any]]:
        """Run a streaming request (propagation) and yield one response per frame."""
        ...


class GpuMemory(Protocol):
    """Reads and frees GPU memory around sessions (torch on a GPU, a fake in tests)."""

    def stats(self) -> dict[str, int]:
        """Return the device's memory counters.

        Returns:
            `allocated_bytes`, `reserved_bytes`, `peak_allocated_bytes`, `free_bytes` and
            `total_bytes`, or `{}` without a GPU.
        """
        ...

    def release(self) -> None:
        """Drop unreachable objects and give cached, unused GPU blocks back to the device."""
        ...

    def reset_peak(self) -> None:
        """Start a new peak measurement (one per session)."""
        ...


class NoGpuMemory:
    """`GpuMemory` without a GPU: no stats, release only collects garbage."""

    def stats(self) -> dict[str, int]:
        """Return no stats."""
        return {}

    def release(self) -> None:
        """Collect garbage."""
        gc.collect()

    def reset_peak(self) -> None:
        """Do nothing."""


class TorchGpuMemory:
    """`GpuMemory` backed by `torch.cuda` (the module is passed in, so tests can fake it)."""

    def __init__(self, torch: Any) -> None:
        """Wrap the `torch` module.

        Args:
            torch: The imported `torch` module.
        """
        self._torch = torch

    def stats(self) -> dict[str, int]:
        """Read the caching allocator's counters and the device's free memory."""
        cuda = self._torch.cuda
        free, total = cuda.mem_get_info()
        return {
            "allocated_bytes": int(cuda.memory_allocated()),
            "reserved_bytes": int(cuda.memory_reserved()),
            "peak_allocated_bytes": int(cuda.max_memory_allocated()),
            "free_bytes": int(free),
            "total_bytes": int(total),
        }

    def release(self) -> None:
        """Collect garbage, clear the autocast weight cache, then empty the CUDA cache."""
        gc.collect()
        self._torch.clear_autocast_cache()
        self._torch.cuda.empty_cache()

    def reset_peak(self) -> None:
        """Reset the peak-allocated counter."""
        self._torch.cuda.reset_peak_memory_stats()


@dataclass(frozen=True)
class EngineSettings:
    """GPU-pod settings read from the environment when the engine loads.

    Attributes:
        memory_guard_bytes: Allocated bytes after a session that trigger the guard; `None` means
            `DEFAULT_MEMORY_GUARD_FRACTION` of the device.
        compile: Whether to build SAM 3.1 with `torch.compile` (and warm it up).
        model_overrides: Tracker attribute -> value (see `MODEL_OVERRIDE_ENVS`).
    """

    memory_guard_bytes: int | None = None
    compile: bool = False
    model_overrides: dict[str, float | int] = field(default_factory=dict)


def engine_settings_from_env(env: Mapping[str, str]) -> EngineSettings:
    """Read `EngineSettings` from environment variables.

    Args:
        env: Usually `os.environ`.

    Returns:
        The settings; unset or empty variables keep the defaults.

    Raises:
        ValueError: If a variable is not a number, or the guard is not positive.
    """
    guard: int | None = None
    if env.get(MEMORY_GUARD_ENV):
        gigabytes = float(env[MEMORY_GUARD_ENV])
        if gigabytes <= 0:
            msg = f"{MEMORY_GUARD_ENV} must be positive"
            raise ValueError(msg)
        guard = int(gigabytes * GIB)
    overrides: dict[str, float | int] = {
        attribute: kind(env[name])
        for name, (attribute, kind) in MODEL_OVERRIDE_ENVS.items()
        if env.get(name)
    }
    compile_model = env.get(COMPILE_ENV, "").strip().lower() in {"1", "true", "yes"}
    return EngineSettings(
        memory_guard_bytes=guard, compile=compile_model, model_overrides=overrides
    )


def apply_model_overrides(model: Any, overrides: Mapping[str, float | int]) -> dict[str, Any]:
    """Set tracker detection settings on a loaded SAM 3.1 model.

    The hotstart thresholds are capped at `hotstart_delay`, as the model's constructor asserts.

    Args:
        model: The predictor's model (`Sam3MultiplexTrackingWithInteractivity`).
        overrides: Attribute -> value; names the model lacks are ignored.

    Returns:
        The effective values of every attribute in `MODEL_OVERRIDE_ENVS` the model has.
    """
    for attribute, value in overrides.items():
        if hasattr(model, attribute):
            setattr(model, attribute, value)
    delay = getattr(model, "hotstart_delay", 0)
    if delay > 0:
        for attribute in _HOTSTART_LIMITED:
            if getattr(model, attribute, 0) > delay:
                setattr(model, attribute, delay)
    return {
        attribute: getattr(model, attribute)
        for attribute, _ in MODEL_OVERRIDE_ENVS.values()
        if hasattr(model, attribute)
    }


class Sam31Engine:
    """Segments one concept in one chunk with SAM 3.1; satisfies `sam_runtime.SamWorker`.

    Text mode prompts the concept text on the start frame. Box mode (identity hand-off) prompts
    each object with one positive click at its box centre: in the open-weights SAM 3.1 API, boxes
    are concept exemplars, while per-object prompts with an `obj_id` are clicks. Negative points
    drop every object whose mask covers them on their frame (the hosted API refines masks
    instead). The start frame is 0 for forward propagation and the last frame for backward;
    backward propagation never yields its start frame (`_get_processing_order` starts one frame
    before it), so that frame comes from the prompt's own answer.

    After every session, also a failed one, GPU memory is released and checked (module
    docstring).
    """

    def __init__(
        self,
        predictor: Sam3Predictor,
        inference_context: Callable[[], AbstractContextManager[object]] = nullcontext,
        *,
        memory: GpuMemory | None = None,
        memory_guard_bytes: int | None = None,
        model_config: Mapping[str, Any] | None = None,
    ) -> None:
        """Wrap a loaded predictor.

        Args:
            predictor: A `sam3` video predictor, or a fake in tests.
            inference_context: Entered around every session (on a GPU: bfloat16 autocast plus
                inference mode, as the sam3 examples run it).
            memory: Reads and frees GPU memory (defaults to `NoGpuMemory`).
            memory_guard_bytes: Allocated bytes after a session that trigger the guard; `None`
                disables it.
            model_config: Effective model settings, reported by `memory_stats` for `/health`.
        """
        self._predictor = predictor
        self._inference_context = inference_context
        self._memory: GpuMemory = memory or NoGpuMemory()
        self._guard_bytes = memory_guard_bytes
        self._model_config = dict(model_config or {})
        self._sessions = 0
        self._guard_trips = 0
        self._last_session: dict[str, int] = {}

    @classmethod
    def load(cls) -> Sam31Engine:  # pragma: no cover - needs a CUDA GPU and the sam3 package
        """Build the SAM 3.1 predictor, downloading the gated checkpoint with `HF_TOKEN`.

        Returns:
            An engine ready to serve calls.
        """
        import torch  # type: ignore[import-not-found]  # noqa: PLC0415
        from sam3.model_builder import (  # type: ignore[import-not-found]  # noqa: PLC0415
            build_sam3_predictor,
        )

        settings = engine_settings_from_env(os.environ)
        predictor = build_sam3_predictor(
            version="sam3.1", use_fa3=False, compile=settings.compile, warm_up=settings.compile
        )
        drop_unsupported_init_kwargs(predictor.model)
        model_config = apply_model_overrides(predictor.model, settings.model_overrides)
        memory = TorchGpuMemory(torch)
        guard = settings.memory_guard_bytes or int(
            memory.stats()["total_bytes"] * DEFAULT_MEMORY_GUARD_FRACTION
        )
        _log.info("sam_engine_loaded guard_bytes=%d model_config=%s", guard, model_config)
        return cls(
            predictor,
            inference_context=lambda: bf16_inference(torch),
            memory=memory,
            memory_guard_bytes=guard,
            model_config={**model_config, "compile": settings.compile},
        )

    @property
    def predictor(self) -> Sam3Predictor:
        """The loaded `sam3` video predictor (its `model.detector` also serves images)."""
        return self._predictor

    @property
    def inference_context(self) -> Callable[[], AbstractContextManager[object]]:
        """The context every GPU call runs in (bfloat16 autocast on a GPU)."""
        return self._inference_context

    @property
    def memory(self) -> GpuMemory:
        """The device memory reader shared with the image engine."""
        return self._memory

    @property
    def memory_guard_bytes(self) -> int | None:
        """Allocated bytes after a call that trigger the guard."""
        return self._guard_bytes

    def segment(self, video: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Segment one chunk.

        Args:
            video: The chunk's MP4 bytes.
            request: A `SamRequest` in JSON form.

        Returns:
            A `SamResponse` in JSON form.
        """
        started = time.perf_counter()
        width, height = int(request["width"]), int(request["height"])
        self._memory.reset_peak()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "chunk.mp4"
                path.write_bytes(video)
                with self._inference_context():
                    frames = self._run_session(path, request)
        finally:
            self._after_session()
        include_confidence = bool(request["include_confidence"])
        objects: list[WireObject] = []
        for frame_idx, outputs in frames:
            objects.extend(frame_objects(frame_idx, outputs, width, height, include_confidence))
        negatives = [(int(p["frame_idx"]), p["x"], p["y"]) for p in request["negative_points"]]
        return {
            "objects": drop_negative_hits(objects, negatives, width, height),
            "frames_processed": len({frame_idx for frame_idx, _ in frames}),
            "gpu_seconds": time.perf_counter() - started,
            "model_version": MODEL_VERSION,
        }

    def memory_stats(self) -> dict[str, Any]:
        """GPU memory now, after the last session, and the guard's counters (for `/health`).

        Returns:
            `allocated_bytes`, `reserved_bytes`, `peak_allocated_bytes`, `free_bytes` and
            `total_bytes` (empty without a GPU), plus `sessions`, `guard_bytes`, `guard_trips`,
            `last_session` (the same counters right after the last cleanup) and `model_config`.
        """
        return {
            **self._memory.stats(),
            "sessions": self._sessions,
            "guard_bytes": self._guard_bytes,
            "guard_trips": self._guard_trips,
            "last_session": dict(self._last_session),
            "model_config": dict(self._model_config),
        }

    def _run_session(self, path: Path, request: dict[str, Any]) -> FrameOutputs:
        start = 0 if request["direction"] == "forward" else int(request["n_frames"]) - 1
        session = self._predictor.handle_request(
            {"type": "start_session", "resource_path": str(path)}
        )
        session_id = session["session_id"]
        prompt_outputs: Mapping[str, Any] | None = None
        try:
            for prompt in prompt_requests(request, start):
                reply = self._predictor.handle_request({**prompt, "session_id": session_id})
                prompt_outputs = reply.get("outputs") or prompt_outputs
            stream = self._predictor.handle_stream_request(
                {
                    "type": "propagate_in_video",
                    "session_id": session_id,
                    "propagation_direction": request["direction"],
                    "start_frame_index": start,
                    # We read each frame once: do not keep its video-resolution masks on the GPU.
                    "evict_cached_frame_outputs": True,
                }
            )
            frames = [(int(r["frame_index"]), r["outputs"]) for r in stream]
        finally:
            # Our own cleanup (gc + empty_cache) runs right after, unconditionally.
            self._predictor.handle_request(
                {"type": "close_session", "session_id": session_id, "run_gc_collect": False}
            )
        return with_prompt_frame(frames, start, prompt_outputs)

    def _after_session(self) -> None:
        """Free the session's GPU memory and trip the guard if allocated memory stays high."""
        self._sessions += 1
        self._memory.release()
        stats = self._memory.stats()
        allocated = stats.get("allocated_bytes", 0)
        if self._guard_bytes is not None and allocated > self._guard_bytes:
            self._guard_trips += 1
            closed = self._close_leftover_sessions()
            self._memory.release()
            stats = self._memory.stats()
            _log.warning(
                "sam_memory_guard allocated_bytes=%d guard_bytes=%d closed_sessions=%d "
                "after_bytes=%d",
                allocated,
                self._guard_bytes,
                closed,
                stats.get("allocated_bytes", 0),
            )
        self._last_session = stats
        if stats:
            _log.info(
                "sam_session_memory allocated_mb=%d reserved_mb=%d peak_mb=%d",
                stats["allocated_bytes"] // 2**20,
                stats["reserved_bytes"] // 2**20,
                stats["peak_allocated_bytes"] // 2**20,
            )

    def _close_leftover_sessions(self) -> int:
        """Close every session the predictor still holds (none should be left)."""
        sessions = getattr(self._predictor, "_all_inference_states", None)
        if not isinstance(sessions, dict):
            return 0
        leftover = list(sessions)
        for session_id in leftover:
            self._predictor.handle_request({"type": "close_session", "session_id": session_id})
        return len(leftover)


class ImagePredictor(Protocol):
    """Runs SAM 3.1 image detection (`Sam3ProcessorPredictor` on a GPU, a fake in tests)."""

    def set_image(self, image: np.ndarray) -> Any:
        """Encode one RGB `uint8` image (H, W, 3) and return its state."""
        ...

    def detect(self, state: Any, concept: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Detect one concept on an encoded image.

        Returns:
            Pixel `xyxy` boxes `(N, 4)`, scores `(N,)` and boolean masks `(N, H, W)`.
        """
        ...


class Sam3ProcessorPredictor:  # pragma: no cover - needs a CUDA GPU and the sam3 package
    """`ImagePredictor` over `sam3.model.sam3_image_processor.Sam3Processor`."""

    def __init__(self, processor: Any) -> None:
        """Wrap a processor built on a loaded SAM 3.1 detector."""
        self._processor = processor

    def set_image(self, image: np.ndarray) -> Any:
        """Encode the image (the processor takes a PIL image for HWC pixels)."""
        from PIL import Image  # noqa: PLC0415 - GPU image only

        return self._processor.set_image(Image.fromarray(image))

    def detect(self, state: Any, concept: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Run the text prompt on the encoded image."""
        self._processor.reset_all_prompts(state)
        out = self._processor.set_text_prompt(prompt=concept, state=state)
        boxes = out["boxes"].float().cpu().numpy().reshape(-1, 4)
        scores = out["scores"].float().cpu().numpy().reshape(-1)
        masks = out["masks"].cpu().numpy().astype(bool)
        return boxes, scores, masks.reshape(len(scores), *masks.shape[-2:])


def decode_jpeg(data: bytes) -> np.ndarray:
    """Decode JPEG or PNG bytes into RGB `uint8` pixels.

    Raises:
        ValueError: If the bytes are not an image.
    """
    import cv2  # noqa: PLC0415 - opencv-python-headless is in both images

    pixels = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if pixels is None:
        msg = "image bytes could not be decoded"
        raise ValueError(msg)
    return np.ascontiguousarray(pixels[:, :, ::-1])


def image_objects(
    image_idx: int,
    concept: str,
    found: tuple[np.ndarray, np.ndarray, np.ndarray],
    *,
    masks: bool,
    include_confidence: bool,
    min_confidence: float = 0.0,
) -> list[WireObject]:
    """Convert one concept's detections on one image into wire objects (best score first).

    Args:
        image_idx: The image's place in the request.
        concept: The concept.
        found: `(boxes, scores, masks)` from `ImagePredictor.detect`.
        masks: Whether to encode masks (otherwise `rle` is empty: boxes only).
        include_confidence: Whether to report the scores.
        min_confidence: Detections scored below it are dropped.

    Returns:
        One dict per object with `image_idx`, `concept`, `object_id` (0.. in score order),
        `rle`, `box_xyxy` (pixels, clipped to the image) and `confidence`.
    """
    boxes, scores, binary = found
    height, width = binary.shape[-2:] if binary.ndim == 3 else (0, 0)  # noqa: PLR2004 - (N, H, W)
    order = [i for i in np.argsort(-scores, kind="stable") if scores[i] >= min_confidence]
    objects = []
    for object_id, i in enumerate(order):
        x0, y0, x1, y1 = (float(v) for v in boxes[i])
        if width and height:
            x0, x1 = (min(max(v, 0.0), float(width)) for v in (x0, x1))
            y0, y1 = (min(max(v, 0.0), float(height)) for v in (y0, y1))
        objects.append(
            {
                "image_idx": image_idx,
                "concept": concept,
                "object_id": object_id,
                "rle": encode_rle(binary[i]) if masks else NO_MASK,
                "box_xyxy": [x0, y0, x1, y1],
                "confidence": float(scores[i]) if include_confidence else None,
            }
        )
    return objects


class Sam31ImageEngine:
    """SAM 3.1 image detection for runtime keyframes; serves `POST /segment_image`.

    Every image is encoded once and each concept detected on it. After every call, also a failed
    one, GPU memory is released and the guard checked, as after a video session.
    """

    def __init__(
        self,
        predictor: ImagePredictor,
        inference_context: Callable[[], AbstractContextManager[object]] = nullcontext,
        *,
        memory: GpuMemory | None = None,
        memory_guard_bytes: int | None = None,
        decode: Callable[[bytes], np.ndarray] = decode_jpeg,
    ) -> None:
        """Wrap a loaded image predictor.

        Args:
            predictor: The detector behind an `ImagePredictor`, or a fake in tests.
            inference_context: Entered around every call (bfloat16 autocast on a GPU).
            memory: Reads and frees GPU memory (defaults to `NoGpuMemory`).
            memory_guard_bytes: Allocated bytes after a call that trigger a second release.
            decode: Decodes an image's bytes into RGB pixels.
        """
        self._predictor = predictor
        self._inference_context = inference_context
        self._memory: GpuMemory = memory or NoGpuMemory()
        self._guard_bytes = memory_guard_bytes
        self._decode = decode
        self._calls = 0
        self._guard_trips = 0

    @classmethod
    def from_video_engine(cls, engine: Sam31Engine) -> Sam31ImageEngine:  # pragma: no cover
        """Serve images with the detector of an already loaded video engine (needs a GPU).

        The confidence floor comes from `SAM_IMAGE_THRESH` (default 0.1); each request cuts
        higher with its `min_confidence`.
        """
        from sam3.model.sam3_image_processor import (  # type: ignore[import-not-found]  # noqa: PLC0415
            Sam3Processor,
        )

        threshold = float(os.environ.get(IMAGE_THRESH_ENV) or DEFAULT_IMAGE_THRESH)
        video_predictor: Any = engine.predictor
        detector = video_predictor.model.detector
        processor = Sam3Processor(detector, confidence_threshold=threshold)
        _log.info("sam_image_engine_loaded threshold=%s", threshold)
        return cls(
            Sam3ProcessorPredictor(processor),
            inference_context=engine.inference_context,
            memory=engine.memory,
            memory_guard_bytes=engine.memory_guard_bytes,
        )

    def segment_images(self, images: list[bytes], request: dict[str, Any]) -> dict[str, Any]:
        """Detect every concept of `request` on every image.

        Args:
            images: JPEG or PNG bytes, in request order.
            request: A `SamImageRequest` in JSON form; without `min_confidence`, detections
                below `LEGACY_MIN_CONFIDENCE` are dropped.

        Returns:
            A `SamImageResponse` in JSON form.

        Raises:
            ValueError: If an image cannot be decoded.
        """
        started = time.perf_counter()
        concepts = [str(c) for c in request["concepts"]]
        masks, confidence = bool(request.get("masks")), bool(request["include_confidence"])
        floor = float(request.get("min_confidence", LEGACY_MIN_CONFIDENCE))
        objects: list[WireObject] = []
        try:
            with self._inference_context():
                for image_idx, data in enumerate(images):
                    state = self._predictor.set_image(self._decode(data))
                    for concept in concepts:
                        found = self._predictor.detect(state, concept)
                        objects.extend(
                            image_objects(
                                image_idx,
                                concept,
                                found,
                                masks=masks,
                                include_confidence=confidence,
                                min_confidence=floor,
                            )
                        )
        finally:
            self._after_call()
        return {
            "objects": objects,
            "images_processed": len(images),
            "gpu_seconds": time.perf_counter() - started,
            "model_version": IMAGE_MODEL_VERSION,
        }

    def memory_stats(self) -> dict[str, Any]:
        """The image engine's own counters for `/health` (device memory is the video engine's)."""
        return {"calls": self._calls, "guard_trips": self._guard_trips}

    def _after_call(self) -> None:
        self._calls += 1
        self._memory.release()
        allocated = self._memory.stats().get("allocated_bytes", 0)
        if self._guard_bytes is not None and allocated > self._guard_bytes:
            self._guard_trips += 1
            self._memory.release()
            _log.warning(
                "sam_image_memory_guard allocated_bytes=%d guard_bytes=%d",
                allocated,
                self._guard_bytes,
            )


def with_prompt_frame(
    frames: FrameOutputs, start: int, prompt_outputs: Mapping[str, Any] | None
) -> FrameOutputs:
    """Add the prompt frame's own outputs when propagation did not yield that frame.

    Forward propagation yields its start frame again; backward propagation starts one frame
    before it (`Sam3MultiplexTracking._get_processing_order` at the pinned commit), so without
    this the last frame of every backward chunk had no objects.

    Args:
        frames: `(frame_idx, outputs)` from propagation.
        start: The prompt frame.
        prompt_outputs: The last `add_prompt` answer's outputs, if any.

    Returns:
        `frames`, plus `(start, prompt_outputs)` when the start frame is missing.
    """
    if prompt_outputs is None or any(frame_idx == start for frame_idx, _ in frames):
        return frames
    return [*frames, (start, prompt_outputs)]


@contextmanager
def bf16_inference(torch: Any) -> Iterator[None]:
    """Run SAM 3.1 the way the sam3 examples do: CUDA bfloat16 autocast in inference mode.

    The SAM 3.1 checkpoint runs in bfloat16; without autocast, linear layers mixing bfloat16
    weights with float32 inputs fail ("mat1 and mat2 must have the same dtype").
    """
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        yield


def drop_unsupported_init_kwargs(model: Any) -> None:
    """Make `model.init_state` ignore keyword arguments it does not declare.

    At the pinned sam3 commit, `Sam3BasePredictor.start_session` always passes
    `offload_state_to_cpu`, but SAM 3.1's `Sam3MultiplexTrackingWithInteractivity.init_state`
    does not accept it, so every session failed with a TypeError on a real GPU. The wrapper only
    drops names the signature lacks, and does nothing once upstream accepts them (or `**kwargs`).
    """
    original = model.init_state
    parameters = inspect.signature(original).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return

    @functools.wraps(original)
    def init_state(*args: Any, **kwargs: Any) -> Any:
        return original(*args, **{k: v for k, v in kwargs.items() if k in parameters})

    model.init_state = init_state


def prompt_requests(request: dict[str, Any], frame_index: int) -> list[dict[str, Any]]:
    """Translate a wire request into `sam3` `add_prompt` requests (without the session id).

    Args:
        request: A `SamRequest` in JSON form.
        frame_index: The frame to prompt on.

    Returns:
        One text prompt, or one positive centre click per box prompt (relative coordinates).
    """
    if request["text"] is not None:
        return [{"type": "add_prompt", "frame_index": frame_index, "text": request["text"]}]
    width, height = float(request["width"]), float(request["height"])
    prompts = []
    for box in request["box_prompts"]:
        x0, y0, x1, y1 = box["box_xyxy"]
        centre = [(x0 + x1) / 2 / width, (y0 + y1) / 2 / height]
        prompts.append(
            {
                "type": "add_prompt",
                "frame_index": frame_index,
                "points": [centre],
                "point_labels": [1],
                "obj_id": int(box["object_id"]),
            }
        )
    return prompts


def frame_objects(
    frame_idx: int,
    outputs: Mapping[str, Any],
    width: int,
    height: int,
    include_confidence: bool,
) -> list[WireObject]:
    """Convert one frame of `sam3` outputs into wire objects.

    Args:
        frame_idx: Frame index inside the chunk.
        outputs: `out_obj_ids`, `out_probs`, `out_boxes_xywh` (relative) and `out_binary_masks`.
        width: Frame width in pixels.
        height: Frame height in pixels.
        include_confidence: Whether to report the per-object probability.

    Returns:
        One dict per object with `frame_idx`, `object_id`, `rle`, `box_xyxy` (pixels) and
        `confidence`.
    """
    ids = np.asarray(outputs["out_obj_ids"]).reshape(-1)
    probs = np.asarray(outputs["out_probs"]).reshape(-1)
    boxes = np.asarray(outputs["out_boxes_xywh"], dtype=float).reshape(-1, 4)
    masks = np.asarray(outputs["out_binary_masks"], dtype=bool)
    objects = []
    for i, object_id in enumerate(ids):
        x, y, w, h = boxes[i]
        objects.append(
            {
                "frame_idx": frame_idx,
                "object_id": int(object_id),
                "rle": encode_rle(masks[i]),
                "box_xyxy": [
                    float(x * width),
                    float(y * height),
                    float((x + w) * width),
                    float((y + h) * height),
                ],
                "confidence": float(probs[i]) if include_confidence else None,
            }
        )
    return objects


def drop_negative_hits(
    objects: list[WireObject],
    negative_points: list[tuple[int, float, float]],
    width: int,
    height: int,
) -> list[WireObject]:
    """Remove every object whose mask covers a negative point on that point's frame.

    Args:
        objects: Wire objects from `frame_objects`.
        negative_points: `(frame_idx, x, y)` in pixels.
        width: Frame width in pixels.
        height: Frame height in pixels.

    Returns:
        The objects whose ids were not hit, in their original order.
    """
    rejected: set[int] = set()
    for frame_idx, x, y in negative_points:
        col, row = int(x), int(y)
        if not (0 <= col < width and 0 <= row < height):
            continue
        for obj in objects:
            if obj["frame_idx"] == frame_idx and decode_rle(obj["rle"])[row, col]:
                rejected.add(obj["object_id"])
    return [obj for obj in objects if obj["object_id"] not in rejected]


def build_app(
    hf_token: str, *, gpu: str = DEFAULT_GPU, min_containers: int = WARM_CONTAINERS
) -> Any:  # pragma: no cover - needs the `ml` extra; exercised by the gpu-marked test
    """Define the Modal app `rulecam-sam` with one warm GPU container.

    Args:
        hf_token: Hugging Face token with access to `facebook/sam3.1`.
        gpu: Modal GPU type ("L4" or "A10G" class is enough).
        min_containers: Containers kept warm so the first call of a chunk is not a cold start.

    Returns:
        The `modal.App`, ready to deploy.
    """
    import modal  # noqa: PLC0415 - the `ml` extra is optional

    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("git")
        .pip_install(*TORCH_PACKAGES, index_url=TORCH_INDEX_URL)
        .pip_install(*IMAGE_PACKAGES)
        .add_local_python_source("rulecam")
    )
    app = modal.App(MODAL_APP_NAME, image=image)

    @app.cls(
        gpu=gpu,
        secrets=[modal.Secret.from_dict({"HF_TOKEN": hf_token})],
        min_containers=min_containers,
        scaledown_window=SCALEDOWN_WINDOW_S,
        timeout=CALL_TIMEOUT_S,
        serialized=True,
    )
    class Sam31Worker:  # the class name is the Modal tag: keep it equal to MODAL_CLASS_NAME
        engine: Sam31Engine

        @modal.enter()  # type: ignore[untyped-decorator]
        def load(self) -> None:
            self.engine = Sam31Engine.load()

        @modal.method()  # type: ignore[untyped-decorator]
        def segment(self, video: bytes, request: dict[str, Any]) -> dict[str, Any]:
            return self.engine.segment(video, request)

    return app


def deploy() -> None:  # pragma: no cover - deploys to Modal; run by hand, never in CI
    """Deploy `rulecam-sam` with `HF_TOKEN` and the Modal tokens from `Settings`.

    Raises:
        PerceptionError: If a token is missing.
    """
    import modal  # noqa: PLC0415 - the `ml` extra is optional

    from rulecam.config import get_settings  # noqa: PLC0415 - keeps the GPU image free of pydantic

    settings = get_settings()
    hf_token, token_id, token_secret = (
        settings.hf_token,
        settings.modal_token_id,
        settings.modal_token_secret,
    )
    if hf_token is None or token_id is None or token_secret is None:
        msg = "HF_TOKEN, MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set to deploy rulecam-sam"
        raise PerceptionError(msg)
    client = modal.Client.from_credentials(
        token_id.get_secret_value(), token_secret.get_secret_value()
    )
    app = build_app(hf_token.get_secret_value())
    with modal.enable_output():
        app.deploy(client=client)


if __name__ == "__main__":  # pragma: no cover
    deploy()
