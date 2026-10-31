from __future__ import annotations

import contextlib
import gc
import json
from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

from rulecam.perception import sam_worker
from rulecam.perception.masks import decode_rle
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
    assert json.loads(first["rle"])["size"] == [HEIGHT, WIDTH]
    assert np.array_equal(decode_rle(first["rle"]).astype(bool), box_mask(1, 1, 4, 3))


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


class _MultiplexModel:
    """Mimics sam3.1's init_state, which lacks offload_state_to_cpu."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def init_state(self, resource_path: str, offload_video_to_cpu: bool = False) -> str:
        self.calls.append({"resource_path": resource_path, "offload": offload_video_to_cpu})
        return "state"


class _KwargsModel:
    def init_state(self, **kwargs: object) -> dict[str, object]:
        return kwargs


def test_drop_unsupported_init_kwargs_ignores_undeclared_names() -> None:
    model = _MultiplexModel()
    sam_worker.drop_unsupported_init_kwargs(model)
    # What Sam3BasePredictor.start_session passes at the pinned commit:
    init_state: Any = model.init_state  # the wrapped callable accepts any keyword
    state = init_state(
        resource_path="v.mp4", offload_video_to_cpu=False, offload_state_to_cpu=False
    )
    assert state == "state"
    assert model.calls == [{"resource_path": "v.mp4", "offload": False}]


def test_drop_unsupported_init_kwargs_leaves_var_kwargs_alone() -> None:
    model = _KwargsModel()
    original = model.init_state
    sam_worker.drop_unsupported_init_kwargs(model)
    assert model.init_state == original
    assert model.init_state(offload_state_to_cpu=True) == {"offload_state_to_cpu": True}


def test_engine_runs_each_session_inside_the_inference_context() -> None:
    entered: list[str] = []

    @contextlib.contextmanager
    def context() -> Iterator[None]:
        entered.append("in")
        yield
        entered.append("out")

    engine = sam_worker.Sam31Engine(FakePredictor([]), inference_context=context)
    engine.segment(b"video", _request())
    assert entered == ["in", "out"]


def test_bf16_inference_enters_inference_mode_and_bf16_autocast() -> None:
    calls: list[tuple[object, ...]] = []

    class _Torch:
        bfloat16 = "bf16"

        @contextlib.contextmanager
        def inference_mode(self) -> Iterator[None]:
            calls.append(("inference_mode",))
            yield

        @contextlib.contextmanager
        def autocast(self, device: str, dtype: object) -> Iterator[None]:
            calls.append(("autocast", device, dtype))
            yield

    with sam_worker.bf16_inference(_Torch()):
        pass
    assert calls == [("inference_mode",), ("autocast", "cuda", "bf16")]


# --- GPU memory per session (live finding: 4 GB -> 22 GB over a few calls, then OOM) ----------


class FakeMemory:
    """Records `GpuMemory` calls; `allocated` is what `stats` reports after each release."""

    def __init__(self, allocated: list[int] | None = None) -> None:
        self.calls: list[str] = []
        self._allocated = list(allocated or [2 * 2**30])

    def stats(self) -> dict[str, int]:
        self.calls.append("stats")
        allocated = self._allocated[0] if len(self._allocated) == 1 else self._allocated.pop(0)
        return {
            "allocated_bytes": allocated,
            "reserved_bytes": allocated + 2**30,
            "peak_allocated_bytes": allocated + 2**29,
            "free_bytes": 10 * 2**30,
            "total_bytes": 22 * 2**30,
        }

    def release(self) -> None:
        self.calls.append("release")

    def reset_peak(self) -> None:
        self.calls.append("reset_peak")


def test_memory_is_released_after_every_session() -> None:
    memory = FakeMemory()
    engine = Sam31Engine(FakePredictor([(0, frame_outputs([]))]), memory=memory)
    for _ in range(3):
        engine.segment(b"x", _request())
    assert memory.calls.count("release") == 3
    assert memory.calls[:3] == ["reset_peak", "release", "stats"]
    stats = engine.memory_stats()
    assert stats["sessions"] == 3
    assert stats["guard_trips"] == 0
    assert stats["last_session"]["allocated_bytes"] == 2 * 2**30
    assert stats["allocated_bytes"] == 2 * 2**30


def test_memory_is_released_when_the_session_fails() -> None:
    class BrokenPredictor(FakePredictor):
        def handle_stream_request(self, request: dict[str, Any]) -> Any:
            raise RuntimeError("CUDA out of memory")

    memory = FakeMemory()
    predictor = BrokenPredictor([])
    engine = Sam31Engine(predictor, memory=memory)
    with pytest.raises(RuntimeError, match="out of memory"):
        engine.segment(b"x", _request())
    assert predictor.open_sessions == set()
    assert "release" in memory.calls
    assert engine.memory_stats()["sessions"] == 1


def test_memory_is_released_when_the_session_cannot_start() -> None:
    class NoStart(FakePredictor):
        def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("CUDA out of memory. Tried to allocate 1.11 GiB")

    memory = FakeMemory()
    with pytest.raises(RuntimeError, match=r"1\.11 GiB"):
        Sam31Engine(NoStart([]), memory=memory).segment(b"x", _request())
    assert memory.calls == ["reset_peak", "release", "stats"]


def test_session_requests_free_per_frame_caches() -> None:
    predictor = FakePredictor([(0, frame_outputs([]))])
    Sam31Engine(predictor).segment(b"x", _request())
    propagate = next(r for r in predictor.requests if r["type"] == "propagate_in_video")
    assert propagate["evict_cached_frame_outputs"] is True
    assert predictor.requests[-1] == {
        "type": "close_session",
        "session_id": "session-1",
        "run_gc_collect": False,
    }


def test_memory_guard_closes_leftover_sessions_and_frees_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class LeakyPredictor(FakePredictor):
        """Keeps a stale session around, as a leaked upstream session would."""

        def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
            reply = super().handle_request(request)
            if request["type"] == "start_session":
                self._all_inference_states["stale"] = {"state": {}}
            return reply

    memory = FakeMemory(allocated=[15 * 2**30, 3 * 2**30, 3 * 2**30])
    predictor = LeakyPredictor([(0, frame_outputs([]))])
    engine = Sam31Engine(predictor, memory=memory, memory_guard_bytes=12 * 2**30)
    with caplog.at_level("WARNING", logger="rulecam.sam_worker"):
        engine.segment(b"x", _request())
    assert predictor.open_sessions == set()
    assert memory.calls.count("release") == 2
    stats = engine.memory_stats()
    assert stats["guard_trips"] == 1
    assert stats["guard_bytes"] == 12 * 2**30
    assert stats["last_session"]["allocated_bytes"] == 3 * 2**30
    assert "sam_memory_guard" in caplog.text
    assert "closed_sessions=1" in caplog.text


def test_memory_guard_stays_quiet_below_the_threshold() -> None:
    memory = FakeMemory(allocated=[3 * 2**30])
    engine = Sam31Engine(FakePredictor([]), memory=memory, memory_guard_bytes=12 * 2**30)
    engine.segment(b"x", _request())
    assert engine.memory_stats()["guard_trips"] == 0
    assert memory.calls.count("release") == 1


def test_memory_stats_without_a_gpu_report_counters_and_model_config() -> None:
    engine = Sam31Engine(FakePredictor([]), model_config={"new_det_thresh": 0.65})
    engine.segment(b"x", _request())
    assert engine.memory_stats() == {
        "sessions": 1,
        "guard_bytes": None,
        "guard_trips": 0,
        "last_session": {},
        "model_config": {"new_det_thresh": 0.65},
    }


def test_no_gpu_memory_collects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    collected: list[bool] = []
    monkeypatch.setattr(gc, "collect", lambda: collected.append(True))
    memory = sam_worker.NoGpuMemory()
    memory.reset_peak()
    memory.release()
    assert collected == [True]
    assert memory.stats() == {}


def test_torch_gpu_memory_reads_counters_and_empties_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(gc, "collect", lambda: calls.append("gc"))

    class _Cuda:
        def mem_get_info(self) -> tuple[int, int]:
            return 7, 22

        def memory_allocated(self) -> int:
            return 4

        def memory_reserved(self) -> int:
            return 5

        def max_memory_allocated(self) -> int:
            return 6

        def empty_cache(self) -> None:
            calls.append("empty_cache")

        def reset_peak_memory_stats(self) -> None:
            calls.append("reset_peak")

    class _Torch:
        cuda = _Cuda()

        def clear_autocast_cache(self) -> None:
            calls.append("autocast")

    memory = sam_worker.TorchGpuMemory(_Torch())
    assert memory.stats() == {
        "allocated_bytes": 4,
        "reserved_bytes": 5,
        "peak_allocated_bytes": 6,
        "free_bytes": 7,
        "total_bytes": 22,
    }
    memory.release()
    memory.reset_peak()
    assert calls == ["gc", "autocast", "empty_cache", "reset_peak"]


# --- request-path parity with hosted SAM ---------------------------------------------------------


def test_backward_text_keeps_the_prompt_frame_propagation_skips() -> None:
    # sam3 backward propagation starts one frame before the prompt frame, so frame 2 only exists
    # in the add_prompt answer.
    predictor = FakePredictor(
        [
            (1, frame_outputs([(3, 0.8, (1, 1, 4, 3))])),
            (0, frame_outputs([(3, 0.7, (1, 1, 4, 3))])),
        ],
        prompt_outputs=frame_outputs([(3, 0.9, (2, 1, 5, 3))]),
    )
    response = Sam31Engine(predictor).segment(b"x", _request(direction="backward"))
    assert predictor.requests[1]["frame_index"] == 2
    frames = sorted(o["frame_idx"] for o in response["objects"])
    assert frames == [0, 1, 2]
    last = next(o for o in response["objects"] if o["frame_idx"] == 2)
    assert last["confidence"] == pytest.approx(0.9)
    assert response["frames_processed"] == 3


def test_forward_does_not_duplicate_the_prompt_frame() -> None:
    predictor = FakePredictor(
        [(0, frame_outputs([(1, 0.9, (1, 1, 4, 3))]))],
        prompt_outputs=frame_outputs([(1, 0.5, (1, 1, 4, 3))]),
    )
    response = Sam31Engine(predictor).segment(b"x", _request())
    assert [(o["frame_idx"], o["confidence"]) for o in response["objects"]] == [
        (0, pytest.approx(0.9))
    ]


def test_with_prompt_frame_ignores_a_missing_prompt_answer() -> None:
    frames: list[tuple[int, Any]] = [(1, frame_outputs([]))]
    assert sam_worker.with_prompt_frame(frames, 2, None) == frames


def test_box_prompt_beyond_the_object_cap_answers_without_outputs() -> None:
    class CappedPredictor(FakePredictor):
        def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
            reply = super().handle_request(request)
            if request["type"] == "add_prompt" and request["obj_id"] == 9:
                return {"frame_index": request["frame_index"], "outputs": None}
            return reply

    predictor = CappedPredictor([], prompt_outputs=frame_outputs([(7, 0.6, (0, 0, 2, 2))]))
    request = _request(
        text=None,
        direction="backward",
        box_prompts=[
            {"object_id": 7, "box_xyxy": [0.0, 0.0, 2.0, 2.0]},
            {"object_id": 9, "box_xyxy": [4.0, 2.0, 8.0, 6.0]},
        ],
    )
    response = Sam31Engine(predictor).segment(b"x", request)
    assert [(o["frame_idx"], o["object_id"]) for o in response["objects"]] == [(2, 7)]


# --- settings read on the GPU pod ---------------------------------------------------------------


def test_engine_settings_default_to_upstream() -> None:
    assert sam_worker.engine_settings_from_env({}) == sam_worker.EngineSettings()


def test_engine_settings_parse_guard_compile_and_overrides() -> None:
    settings = sam_worker.engine_settings_from_env(
        {
            "SAM_MEMORY_GUARD_GB": "12.5",
            "SAM_COMPILE": "true",
            "SAM_NEW_DET_THRESH": "0.5",
            "SAM_HOTSTART_DELAY": "0",
            "SAM_DET_THRESH": "",
        }
    )
    assert settings.memory_guard_bytes == int(12.5 * 2**30)
    assert settings.compile is True
    assert settings.model_overrides == {"new_det_thresh": 0.5, "hotstart_delay": 0}


@pytest.mark.parametrize("value", ["0", "-1"])
def test_engine_settings_reject_a_non_positive_guard(value: str) -> None:
    with pytest.raises(ValueError, match="SAM_MEMORY_GUARD_GB"):
        sam_worker.engine_settings_from_env({"SAM_MEMORY_GUARD_GB": value})


def test_apply_model_overrides_caps_hotstart_thresholds() -> None:
    class _Model:
        score_threshold_detection = 0.4
        new_det_thresh = 0.65
        hotstart_delay = 15
        hotstart_unmatch_thresh = 8
        hotstart_dup_thresh = 8

    model = _Model()
    effective = sam_worker.apply_model_overrides(
        model, {"hotstart_delay": 4, "new_det_thresh": 0.5, "unknown": 1}
    )
    assert effective == {
        "score_threshold_detection": 0.4,
        "new_det_thresh": 0.5,
        "hotstart_delay": 4,
        "hotstart_unmatch_thresh": 4,
        "hotstart_dup_thresh": 4,
    }
    assert not hasattr(model, "unknown")


def test_apply_model_overrides_leaves_thresholds_when_hotstart_is_off() -> None:
    class _Model:
        hotstart_delay = 15
        hotstart_unmatch_thresh = 8

    model = _Model()
    assert sam_worker.apply_model_overrides(model, {"hotstart_delay": 0}) == {
        "hotstart_delay": 0,
        "hotstart_unmatch_thresh": 8,
    }
