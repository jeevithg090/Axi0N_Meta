"""A fake of the `sam3` video predictor API (`handle_request` / `handle_stream_request`)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

WIDTH, HEIGHT = 8, 6


def box_mask(x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


def frame_outputs(objects: list[tuple[int, float, tuple[int, int, int, int]]]) -> dict[str, Any]:
    """`sam3` outputs for one frame: (object id, probability, pixel box) per object."""
    if not objects:
        return {
            "out_obj_ids": np.zeros(0, dtype=np.int64),
            "out_probs": np.zeros(0, dtype=np.float32),
            "out_boxes_xywh": np.zeros((0, 4), dtype=np.float32),
            "out_binary_masks": np.zeros((0, HEIGHT, WIDTH), dtype=bool),
        }
    ids, probs, boxes, masks = [], [], [], []
    for object_id, prob, (x0, y0, x1, y1) in objects:
        ids.append(object_id)
        probs.append(prob)
        boxes.append([x0 / WIDTH, y0 / HEIGHT, (x1 - x0) / WIDTH, (y1 - y0) / HEIGHT])
        masks.append(box_mask(x0, y0, x1, y1))
    return {
        "out_obj_ids": np.array(ids, dtype=np.int64),
        "out_probs": np.array(probs, dtype=np.float32),
        "out_boxes_xywh": np.array(boxes, dtype=np.float32),
        "out_binary_masks": np.stack(masks),
    }


class FakePredictor:
    """Yields preset per-frame outputs in the given order and records every request.

    Like `Sam3BasePredictor`, open sessions live in `_all_inference_states`.
    """

    def __init__(
        self,
        frames: list[tuple[int, dict[str, Any]]],
        prompt_outputs: dict[str, Any] | None = None,
    ) -> None:
        self._frames = frames
        self._prompt_outputs = prompt_outputs
        self.requests: list[dict[str, Any]] = []
        self.video_bytes: bytes | None = None
        self._all_inference_states: dict[str, dict[str, Any]] = {}

    @property
    def open_sessions(self) -> set[str]:
        return set(self._all_inference_states)

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        if request["type"] == "start_session":
            self.video_bytes = Path(request["resource_path"]).read_bytes()
            self._all_inference_states["session-1"] = {"state": {}}
            return {"session_id": "session-1"}
        if request["type"] == "close_session":
            self._all_inference_states.pop(request["session_id"], None)
            return {"is_success": True}
        outputs = self._prompt_outputs if self._prompt_outputs is not None else frame_outputs([])
        return {"frame_index": request["frame_index"], "outputs": outputs}

    def handle_stream_request(self, request: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.requests.append(request)
        for frame_index, outputs in self._frames:
            yield {"frame_index": frame_index, "outputs": outputs}
