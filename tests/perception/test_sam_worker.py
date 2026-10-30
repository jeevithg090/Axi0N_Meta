from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from rulecam.perception import rle
from rulecam.perception.sam_worker import (
    MODEL_VERSION,
    Sam3Predictor,
    Sam31Engine,
    drop_negative_hits,
    frame_objects,
    prompt_requests,
)
from tests.perception.fake_sam3 import HEIGHT, WIDTH, FakePredictor, box_mask, frame_outputs


def _request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "text": "person",
        "box_prompts": [],
        "negative_points": [],
        "direction": "forward",
        "include_confidence": True,
        "width": WIDTH,
        "height": HEIGHT,
        "n_frames": 3,
        "video_sha256": "0" * 64,
    }
    request.update(overrides)
    return request


def test_text_prompt_runs_one_session_forward_from_frame_zero() -> None:
    predictor = FakePredictor(
        [
            (0, frame_outputs([(1, 0.9, (1, 1, 4, 3))])),
            (1, frame_outputs([(1, 0.8, (2, 1, 5, 3))])),
            (2, frame_outputs([])),
        ]
    )
    response = Sam31Engine(predictor).segment(b"mp4-bytes", _request())

    types = [r["type"] for r in predictor.requests]
    assert types == ["start_session", "add_prompt", "propagate_in_video", "close_session"]
    assert predictor.requests[1] == {
        "type": "add_prompt",
        "frame_index": 0,
        "text": "person",
        "session_id": "session-1",
    }
    assert predictor.requests[2]["propagation_direction"] == "forward"
    assert predictor.requests[2]["start_frame_index"] == 0
    assert predictor.video_bytes == b"mp4-bytes"
    assert predictor.open_sessions == set()

    assert response["frames_processed"] == 3
    assert response["model_version"] == MODEL_VERSION
    assert response["gpu_seconds"] >= 0
    first = response["objects"][0]
    assert first["frame_idx"] == 0
    assert first["object_id"] == 1
    assert first["box_xyxy"] == pytest.approx([1.0, 1.0, 4.0, 3.0])
    assert first["confidence"] == pytest.approx(0.9)
    assert np.array_equal(rle.decode(first["rle"], HEIGHT, WIDTH), box_mask(1, 1, 4, 3))


def test_backward_box_prompts_click_each_object_on_the_last_frame() -> None:
    predictor = FakePredictor([(2, frame_outputs([(7, 0.6, (0, 0, 2, 2))]))])
    request = _request(
        text=None,
        direction="backward",
        include_confidence=False,
        box_prompts=[
            {"object_id": 7, "box_xyxy": [0.0, 0.0, 2.0, 2.0]},
            {"object_id": 9, "box_xyxy": [4.0, 2.0, 8.0, 6.0]},
        ],
    )
    response = Sam31Engine(predictor).segment(b"x", request)

    prompts = [r for r in predictor.requests if r["type"] == "add_prompt"]
    assert [p["obj_id"] for p in prompts] == [7, 9]
    assert all(p["frame_index"] == 2 and p["point_labels"] == [1] for p in prompts)
    assert prompts[1]["points"] == [[6.0 / WIDTH, 4.0 / HEIGHT]]
    assert predictor.requests[-2]["propagation_direction"] == "backward"
    assert response["objects"][0]["confidence"] is None


def test_session_is_closed_when_propagation_fails() -> None:
    class BrokenPredictor(FakePredictor):
        def handle_stream_request(self, request: dict[str, Any]) -> Any:
            raise RuntimeError("CUDA out of memory")

    predictor = BrokenPredictor([])
    with pytest.raises(RuntimeError, match="out of memory"):
        Sam31Engine(predictor).segment(b"x", _request())
    assert predictor.requests[-1]["type"] == "close_session"


def test_negative_point_drops_the_object_it_hits() -> None:
    outputs = frame_outputs([(1, 0.9, (0, 0, 3, 3)), (2, 0.9, (5, 3, 8, 6))])
    objects = frame_objects(1, outputs, WIDTH, HEIGHT, True)
    kept = drop_negative_hits(objects, [(1, 6.5, 4.5), (1, 100.0, 100.0)], WIDTH, HEIGHT)
    assert [o["object_id"] for o in kept] == [1]
    assert drop_negative_hits(objects, [(0, 6.5, 4.5)], WIDTH, HEIGHT) == objects


def test_engine_applies_negative_points_across_the_chunk() -> None:
    predictor = FakePredictor(
        [
            (0, frame_outputs([(4, 0.9, (5, 3, 8, 6))])),
            (1, frame_outputs([(4, 0.9, (5, 3, 8, 6)), (5, 0.9, (0, 0, 2, 2))])),
        ]
    )
    request = _request(
        text=None,
        box_prompts=[{"object_id": 4, "box_xyxy": [5.0, 3.0, 8.0, 6.0]}],
        negative_points=[{"frame_idx": 1, "x": 6.0, "y": 4.0}],
    )
    response = Sam31Engine(predictor).segment(b"x", request)
    assert [o["object_id"] for o in response["objects"]] == [5]


def test_frame_without_objects_gives_nothing() -> None:
    assert frame_objects(0, frame_outputs([]), WIDTH, HEIGHT, True) == []


def test_prompt_requests_for_text() -> None:
    assert prompt_requests(_request(), 0) == [
        {"type": "add_prompt", "frame_index": 0, "text": "person"}
    ]


def test_fake_predictor_conforms_to_the_sam3_api() -> None:
    predictor: Sam3Predictor = FakePredictor([])
    assert predictor.handle_request({"type": "start_session", "resource_path": __file__})
