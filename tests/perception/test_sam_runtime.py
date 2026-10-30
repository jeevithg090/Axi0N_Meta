from __future__ import annotations

import asyncio
import hashlib
import inspect
import sys
from collections.abc import Callable, Iterator
from contextlib import aclosing
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np
import pytest

from rulecam.bus import Bus, Message, Topic
from rulecam.config import Settings
from rulecam.contracts import Detection, Segmenter
from rulecam.perception.errors import SamResponseError, SamRuntimeUnavailableError
from rulecam.perception.metrics import PerceptionMetrics
from rulecam.perception.sam_runtime import (
    MODEL_NAME,
    ModalSam31,
    ModalWorker,
    SamRequest,
    SamWorker,
    build_requests,
    prompt_batches,
)
from rulecam.perception.sam_worker import Sam31Engine
from tests.fakes import FakeSegmenter, make_chunk
from tests.perception.fake_sam3 import FakePredictor, frame_outputs

VIDEO = b"fake mp4 bytes"


def _object(frame_idx: int, object_id: int = 1, confidence: float | None = 0.9) -> dict[str, Any]:
    return {
        "frame_idx": frame_idx,
        "object_id": object_id,
        "rle": "9220003",
        "box_xyxy": [2.0, 1.0, 5.0, 3.0],
        "confidence": confidence,
    }


def _response(*objects: dict[str, Any], gpu_seconds: float = 0.25) -> dict[str, Any]:
    return {
        "objects": list(objects),
        "frames_processed": 30,
        "gpu_seconds": gpu_seconds,
        "model_version": "fake",
    }


class FakeWorker:
    """Answers each call with `respond(request)` and records the calls."""

    def __init__(self, respond: Callable[[dict[str, Any]], Any] | None = None) -> None:
        self._respond = respond or (lambda request: _response())
        self.calls: list[tuple[bytes, dict[str, Any]]] = []

    def segment(self, video: bytes, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((video, request))
        result: dict[str, Any] = self._respond(request)
        return result


def _sam(worker: SamWorker, **kwargs: Any) -> ModalSam31:
    return ModalSam31(worker, read_video=lambda path: VIDEO, **kwargs)


def test_conforms_to_the_segmenter_protocol() -> None:
    segmenter: Segmenter = _sam(FakeWorker())
    worker: SamWorker = Sam31Engine(FakePredictor([]))
    assert segmenter.segment(make_chunk(), "person") == []
    assert worker is not None


def test_segment_signature_matches_the_contract_and_the_fake() -> None:
    def params(fn: Callable[..., Any]) -> list[tuple[str, Any, Any]]:
        sig = inspect.signature(fn)
        return [(p.name, p.kind, p.default) for p in sig.parameters.values()]

    assert params(ModalSam31.segment) == params(Segmenter.segment)
    assert params(ModalSam31.segment) == params(FakeSegmenter.segment)


def test_normal_response_becomes_detections_with_masks() -> None:
    worker = FakeWorker(lambda request: _response(_object(0), _object(5, object_id=2)))
    chunk = make_chunk(fps=10)
    detections = _sam(worker).segment(chunk, "hard hat")

    assert [(d.frame_idx, d.object_id) for d in detections] == [(0, 1), (5, 2)]
    second = detections[1]
    assert second == Detection(
        camera_id="cam1",
        frame_idx=5,
        ts=chunk.start_ts + timedelta(seconds=0.5),
        concept="hard hat",
        object_id=2,
        mask=second.mask,
    )
    assert second.mask.rle == "9220003"
    assert second.mask.box_xyxy == (2.0, 1.0, 5.0, 3.0)
    assert second.mask.confidence == 0.9
    _, request = worker.calls[0]
    assert request["text"] == "hard hat"
    assert request["video_sha256"] == hashlib.sha256(VIDEO).hexdigest()
    assert (request["width"], request["height"], request["n_frames"]) == (1280, 720, 30)


def test_empty_completion_returns_no_detections() -> None:
    assert _sam(FakeWorker(lambda request: _response())).segment(make_chunk(), "forklift") == []


def test_out_of_order_frames_come_back_sorted() -> None:
    worker = FakeWorker(lambda request: _response(_object(9), _object(2, 3), _object(2, 1)))
    detections = _sam(worker).segment(make_chunk(), "person", box_prompts={1: (0, 0, 9, 9)})
    assert [(d.frame_idx, d.object_id) for d in detections] == [(2, 1), (2, 3), (9, 1)]


def test_text_with_point_prompts_raises_before_any_call() -> None:
    worker = FakeWorker()
    with pytest.raises(ValueError, match="cannot be combined with a text prompt"):
        _sam(worker).segment(make_chunk(), "harness", negative_points=[(3, 10.0, 20.0)])
    assert worker.calls == []


def test_box_prompts_take_negative_points_and_direction() -> None:
    worker = FakeWorker()
    _sam(worker).segment(
        make_chunk(),
        "harness",
        box_prompts={4: (1.0, 2.0, 3.0, 4.0)},
        negative_points=[(3, 10.0, 20.0)],
        direction="backward",
        include_confidence=False,
    )
    _, request = worker.calls[0]
    assert request["text"] is None
    assert request["box_prompts"] == [{"object_id": 4, "box_xyxy": [1.0, 2.0, 3.0, 4.0]}]
    assert request["negative_points"] == [{"frame_idx": 3, "x": 10.0, "y": 20.0}]
    assert request["direction"] == "backward"
    assert request["include_confidence"] is False


_BOX = {"object_id": 1, "box_xyxy": (0, 0, 1, 1)}
_MANY_BOXES = tuple({"object_id": i, "box_xyxy": (0, 0, 1, 1)} for i in range(17))
_LATE_POINT = {"frame_idx": 30, "x": 1, "y": 1}


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"text": None}, "text prompt or box prompts"),
        ({"text": "person", "box_prompts": (_BOX,)}, "never both"),
        ({"text": None, "box_prompts": _MANY_BOXES}, "at most 16"),
        ({"text": None, "box_prompts": (_BOX,), "negative_points": (_LATE_POINT,)}, "outside"),
        ({"text": ""}, "at least 1 character"),
    ],
)
def test_request_validation(kwargs: dict[str, Any], match: str) -> None:
    base: dict[str, Any] = {"width": 1280, "height": 720, "n_frames": 30, "video_sha256": "x"}
    with pytest.raises(ValueError, match=match):
        SamRequest.model_validate(base | kwargs)


def test_twenty_box_prompts_are_split_into_16_and_4() -> None:
    worker = FakeWorker()
    boxes = {i: (float(i), 0.0, float(i) + 1, 1.0) for i in range(20)}
    _sam(worker).segment(make_chunk(), "person", box_prompts=boxes)

    sizes = [len(request["box_prompts"]) for _, request in worker.calls]
    assert sizes == [16, 4]
    assert worker.calls[1][1]["box_prompts"][0]["object_id"] == 16


def test_prompt_batches_without_boxes() -> None:
    assert prompt_batches(None) == []
    assert prompt_batches({}) == []
    assert len(build_requests(make_chunk(), "person", video_sha256="x")) == 1


async def test_relay_model_used_is_published_with_latency() -> None:
    bus = Bus()
    ticks: Iterator[float] = iter([10.0, 10.125])
    sam = _sam(FakeWorker(), bus=bus, timer=lambda: next(ticks))

    async def first_message() -> Message:
        async with aclosing(bus.subscribe(Topic.RELAY_MODEL_USED)) as messages:
            async for message in messages:
                return message
        raise AssertionError

    listener = asyncio.create_task(first_message())
    await asyncio.sleep(0)
    sam.segment(make_chunk(), "person")
    message = await asyncio.wait_for(listener, timeout=1)
    assert message.payload == {"model": MODEL_NAME, "segment": "perception", "ms": 125}


def test_calls_are_recorded_in_metrics() -> None:
    metrics = PerceptionMetrics()
    _sam(FakeWorker(lambda request: _response(gpu_seconds=0.5)), metrics=metrics).segment(
        make_chunk(), "person", box_prompts=dict.fromkeys(range(20), (0, 0, 1, 1))
    )
    camera = metrics.report().cameras[0]
    assert camera.sam_calls_self_hosted == 2
    assert camera.gpu_seconds == pytest.approx(1.0)


@pytest.mark.parametrize(
    "raw",
    [{"objects": "nope"}, _response(_object(30)), None],
    ids=["malformed", "frame-outside-chunk", "none"],
)
def test_bad_worker_response_raises(raw: Any) -> None:
    with pytest.raises(SamResponseError):
        _sam(FakeWorker(lambda request: raw)).segment(make_chunk(), "person")


def test_end_to_end_with_the_gpu_engine_and_a_fake_predictor() -> None:
    predictor = FakePredictor(
        [
            (1, frame_outputs([(3, 0.7, (2, 1, 6, 5))])),
            (0, frame_outputs([(3, 0.8, (1, 1, 5, 5))])),
        ]
    )
    chunk = make_chunk(n_frames=2).model_copy(update={"width": 8, "height": 6})
    detections = _sam(Sam31Engine(predictor)).segment(chunk, "box", box_prompts={3: (1, 1, 5, 5)})

    assert [(d.frame_idx, d.object_id) for d in detections] == [(0, 3), (1, 3)]
    assert detections[0].mask.box_xyxy == pytest.approx((1.0, 1.0, 5.0, 5.0))
    assert predictor.video_bytes == VIDEO


def test_reads_the_chunk_file(tmp_path: Path) -> None:
    path = tmp_path / "cam1_000000.mp4"
    path.write_bytes(b"chunk")
    worker = FakeWorker()
    ModalSam31(worker).segment(make_chunk().model_copy(update={"path": str(path)}), "person")
    assert worker.calls[0][0] == b"chunk"


# --- ModalWorker (no network: `modal` is replaced by a stub) --------------------------------


class _ModalError(Exception):
    pass


def _stub_modal(remote: Callable[[bytes, dict[str, Any]], Any]) -> SimpleNamespace:
    calls: dict[str, list[Any]] = {"from_name": [], "from_credentials": []}

    def from_name(app: str, cls: str, *, client: Any) -> Any:
        calls["from_name"].append((app, cls, client))
        instance = SimpleNamespace(segment=SimpleNamespace(remote=remote))
        return lambda: instance

    def from_credentials(token_id: str, token_secret: str) -> str:
        calls["from_credentials"].append((token_id, token_secret))
        return "client"

    return SimpleNamespace(
        exception=SimpleNamespace(Error=_ModalError),
        Cls=SimpleNamespace(from_name=from_name),
        Client=SimpleNamespace(from_credentials=from_credentials),
        calls=calls,
    )


def test_modal_worker_without_the_ml_extra_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "modal", None)
    with pytest.raises(SamRuntimeUnavailableError, match="ml"):
        ModalWorker(Settings(_env_file=None)).segment(VIDEO, {})


def test_modal_errors_become_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def remote(video: bytes, request: dict[str, Any]) -> Any:
        raise _ModalError("app rulecam-sam not found")

    monkeypatch.setitem(sys.modules, "modal", _stub_modal(remote))
    with pytest.raises(SamRuntimeUnavailableError, match="rulecam-sam/Sam31Worker"):
        ModalWorker(Settings(_env_file=None)).segment(VIDEO, {})


def test_modal_worker_calls_the_deployed_class_with_settings_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _stub_modal(lambda video, request: _response())
    monkeypatch.setitem(sys.modules, "modal", stub)
    settings = Settings(
        _env_file=None,
        modal_token_id="id",  # noqa: S106 - placeholder, the Modal stub never checks it
        modal_token_secret="secret",  # noqa: S106
    )
    worker = ModalWorker(settings)

    assert worker.segment(VIDEO, {}) == _response()
    assert worker.segment(VIDEO, {}) == _response()
    assert stub.calls["from_name"] == [("rulecam-sam", "Sam31Worker", "client")]
    assert stub.calls["from_credentials"] == [("id", "secret")]


def test_modal_worker_falls_back_to_modal_config(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _stub_modal(lambda video, request: _response())
    monkeypatch.setitem(sys.modules, "modal", stub)
    ModalWorker(Settings(_env_file=None)).segment(VIDEO, {})
    assert stub.calls["from_name"] == [("rulecam-sam", "Sam31Worker", None)]


@pytest.mark.gpu
def test_real_modal_call_on_a_synthetic_chunk(tmp_path: Path) -> None:
    path = tmp_path / "cam1_000000.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), 10, (320, 240))
    for i in range(30):
        frame = np.full((240, 320, 3), 200, dtype=np.uint8)
        cv2.circle(frame, (60 + 6 * i, 120), 30, (0, 0, 255), -1)
        writer.write(frame)
    writer.release()
    chunk = make_chunk().model_copy(update={"path": str(path), "width": 320, "height": 240})

    metrics = PerceptionMetrics()
    detections = ModalSam31(metrics=metrics).segment(chunk, "red ball")

    assert detections, "SAM 3.1 should find the red ball"
    assert all(d.concept == "red ball" for d in detections)
    assert metrics.report().total.gpu_seconds > 0
