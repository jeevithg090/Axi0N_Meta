"""GPU side of the self-hosted SAM 3.1 runtime: the Modal app `rulecam-sam` (task P1-03).

`Sam31Engine` runs Meta's open-weights SAM 3.1 (`facebook/sam3.1`, Object Multiplex) through the
`sam3` package's predictor API (`handle_request` / `handle_stream_request`). The checkpoint has no
Hugging Face Transformers integration, so the GPU image installs `sam3` from GitHub at a pinned
commit. `sam3` needs numpy < 2, which is why it lives only in the GPU image and never in the
repo's lock. The gated checkpoint is downloaded inside the container with the `HF_TOKEN` taken from
`Settings` at deploy time.

This module imports with the base dependencies only. It must stay free of pydantic and structlog
because the GPU image ships just numpy and `rulecam.perception.masks` from this repo; `modal`,
`torch` and `sam3` are imported lazily inside the functions that need them.

The request and response dicts are the JSON form of `sam_runtime.SamRequest` and
`sam_runtime.SamResponse`. Deploy once per Modal workspace (needs `uv sync --extra ml`, plus
`HF_TOKEN`, `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in `.env`):

    uv run python -m rulecam.perception.sam_worker
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from rulecam.perception.errors import PerceptionError
from rulecam.perception.masks import decode_rle, encode_rle

MODAL_APP_NAME = "rulecam-sam"
MODAL_CLASS_NAME = "Sam31Worker"
MODEL_VERSION = "facebook/sam3.1@sam3.1_multiplex"
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

WireObject = dict[str, Any]


class Sam3Predictor(Protocol):
    """The subset of the `sam3` video predictor API the engine uses."""

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run one session request (start, add prompt, close)."""
        ...

    def handle_stream_request(self, request: dict[str, Any]) -> Iterable[dict[str, Any]]:
        """Run a streaming request (propagation) and yield one response per frame."""
        ...


class Sam31Engine:
    """Segments one concept in one chunk with SAM 3.1; satisfies `sam_runtime.SamWorker`.

    Text mode prompts the concept text on the start frame. Box mode (identity hand-off) prompts
    each object with one positive click at its box centre: in the open-weights SAM 3.1 API, boxes
    are concept exemplars, while per-object prompts with an `obj_id` are clicks. Negative points
    drop every object whose mask covers them on their frame (the hosted API refines masks
    instead). The start frame is 0 for forward propagation and the last frame for backward.
    """

    def __init__(self, predictor: Sam3Predictor) -> None:
        """Wrap a loaded predictor.

        Args:
            predictor: A `sam3` video predictor, or a fake in tests.
        """
        self._predictor = predictor

    @classmethod
    def load(cls) -> Sam31Engine:  # pragma: no cover - needs a CUDA GPU and the sam3 package
        """Build the SAM 3.1 predictor, downloading the gated checkpoint with `HF_TOKEN`.

        Returns:
            An engine ready to serve calls.
        """
        from sam3.model_builder import (  # type: ignore[import-not-found]  # noqa: PLC0415
            build_sam3_predictor,
        )

        return cls(build_sam3_predictor(version="sam3.1", use_fa3=False))

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
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunk.mp4"
            path.write_bytes(video)
            frames = list(self._run_session(path, request))
        include_confidence = bool(request["include_confidence"])
        objects: list[WireObject] = []
        for frame_idx, outputs in frames:
            objects.extend(frame_objects(frame_idx, outputs, width, height, include_confidence))
        negatives = [(int(p["frame_idx"]), p["x"], p["y"]) for p in request["negative_points"]]
        return {
            "objects": drop_negative_hits(objects, negatives, width, height),
            "frames_processed": len(frames),
            "gpu_seconds": time.perf_counter() - started,
            "model_version": MODEL_VERSION,
        }

    def _run_session(
        self, path: Path, request: dict[str, Any]
    ) -> Iterator[tuple[int, Mapping[str, Any]]]:
        start = 0 if request["direction"] == "forward" else int(request["n_frames"]) - 1
        session = self._predictor.handle_request(
            {"type": "start_session", "resource_path": str(path)}
        )
        session_id = session["session_id"]
        try:
            for prompt in prompt_requests(request, start):
                self._predictor.handle_request({**prompt, "session_id": session_id})
            stream = self._predictor.handle_stream_request(
                {
                    "type": "propagate_in_video",
                    "session_id": session_id,
                    "propagation_direction": request["direction"],
                    "start_frame_index": start,
                }
            )
            for response in stream:
                yield int(response["frame_index"]), response["outputs"]
        finally:
            self._predictor.handle_request({"type": "close_session", "session_id": session_id})


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
