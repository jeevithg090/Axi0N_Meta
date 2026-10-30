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
    """Yields preset per-frame outputs in the given order and records every request."""

    def __init__(self, frames: list[tuple[int, dict[str, Any]]]) -> None:
        self._frames = frames
        self.requests: list[dict[str, Any]] = []
        self.video_bytes: bytes | None = None
        self.open_sessions: set[str] = set()

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        if request["type"] == "start_session":
            self.video_bytes = Path(request["resource_path"]).read_bytes()
            self.open_sessions.add("session-1")
            return {"session_id": "session-1"}
        if request["type"] == "close_session":
            self.open_sessions.discard(request["session_id"])
            return {"is_success": True}
        return {"frame_index": request["frame_index"], "outputs": frame_outputs([])}

    def handle_stream_request(self, request: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.requests.append(request)
        for frame_index, outputs in self._frames:
            yield {"frame_index": frame_index, "outputs": outputs}
