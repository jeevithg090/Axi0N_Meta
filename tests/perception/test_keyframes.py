"""Runtime keyframes: sampling, decoding and the hosted / self-hosted image segmenters (WS2b)."""

from __future__ import annotations

import itertools
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulecam.bus import Topic
from rulecam.clients.errors import ModelHTTPError
from rulecam.contracts import Chunk, Detection, Mask
from rulecam.perception.errors import PerceptionError, SamResponseError
from rulecam.perception.keyframes import (
    HostedKeyframes,
    Keyframe,
    KeyframeRates,
    SelfHostedKeyframes,
    decode_keyframes,
    frame_ts,
    keyframe_indices,
)
from rulecam.perception.metrics import HOSTED_USD_PER_IMAGE, Backend, PerceptionMetrics
from rulecam.perception.sam_runtime import (
    MAX_IMAGES_PER_CALL,
    SamImageRequest,
    parse_image_response,
)
from rulecam.rules.timeline import HOLD_S
from tests.fakes import make_chunk
from tests.runtime.fakes import RecordingBus

# --- sampling ---------------------------------------------------------------------------------


def test_keyframe_indices_for_a_ten_fps_chunk() -> None:
    assert keyframe_indices(30, 10.0, 2.0) == (0, 5, 10, 15, 20, 25)
    assert keyframe_indices(30, 10.0, 3.0) == (0, 3, 6, 9, 12, 15, 18, 21, 24, 27)
    assert keyframe_indices(30, 10.0, 4.0) == tuple(range(0, 30, 2))
    assert keyframe_indices(30, 10.0, 10.0) == tuple(range(30))
    assert keyframe_indices(5, 2.0, 4.0) == (0, 1, 2, 3, 4)  # rate above fps: every frame
    assert keyframe_indices(0, 10.0, 2.0) == ()


@given(
    fps=st.sampled_from([5.0, 8.0, 10.0, 12.0, 15.0, 24.0, 25.0, 29.97, 30.0]),
    keyframe_fps=st.floats(min_value=2.0, max_value=4.0),
    seconds=st.sampled_from([2.0, 3.0, 4.0]),
)
def test_keyframes_are_never_further_apart_than_the_timeline_hold(
    fps: float, keyframe_fps: float, seconds: float
) -> None:
    """Also across the border into the next chunk, so tracks interpolate, never flicker."""
    n_frames = round(seconds * fps)
    indices = keyframe_indices(n_frames, fps, keyframe_fps)
    assert indices[0] == 0
    times = [*(i / fps for i in indices), n_frames / fps]  # ends with the next chunk's frame 0
    spacing = [b - a for a, b in itertools.pairwise(times)]
    assert max(spacing) <= min(1 / keyframe_fps, HOLD_S) + 1e-9
    assert len(indices) >= n_frames / fps * keyframe_fps - 1e-9


def test_rates_parse_per_camera_overrides() -> None:
    rates = KeyframeRates.parse(2.0, " dock=4, kitchen = 3 ,")
    assert rates.fps_for("dock") == 4.0
    assert rates.fps_for("kitchen") == 3.0
    assert rates.fps_for("other") == 2.0
    assert KeyframeRates.parse(3.0, "").per_camera == {}


@pytest.mark.parametrize(
    ("default", "spec", "match"),
    [
        (2.0, "dock", "camera_id=fps"),
        (2.0, "=3", "camera_id=fps"),
        (2.0, "dock=1", "outside"),
        (12.0, "", "outside"),
    ],
)
def test_rates_reject_bad_specs(default: float, spec: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        KeyframeRates.parse(default, spec)


# --- decoding ---------------------------------------------------------------------------------


def _video(path: Path, n_frames: int = 12, size: tuple[int, int] = (64, 48)) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), 10, size)
    for i in range(n_frames):
        frame = np.full((size[1], size[0], 3), 20 * i % 255, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path


def _chunk(path: Path, n_frames: int = 12) -> Chunk:
    return make_chunk(n_frames=n_frames).model_copy(
        update={"path": str(path), "width": 64, "height": 48}
    )


def test_decode_keyframes_reads_only_the_chosen_frames_as_jpeg(tmp_path: Path) -> None:
    chunk = _chunk(_video(tmp_path / "c.mp4"))
    frames = decode_keyframes(chunk, (0, 5, 10))
    assert [f.frame_idx for f in frames] == [0, 5, 10]
    assert [f.ts for f in frames] == [frame_ts(chunk, i) for i in (0, 5, 10)]
    for frame in frames:
        assert frame.jpeg[:2] == b"\xff\xd8"  # JPEG
        image = cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_COLOR)
        assert image is not None
        assert image.shape == (48, 64, 3)


def test_decode_keyframes_of_a_short_file_returns_what_exists(tmp_path: Path) -> None:
    chunk = _chunk(_video(tmp_path / "c.mp4", n_frames=4), n_frames=30)
    assert [f.frame_idx for f in decode_keyframes(chunk, (0, 3, 6, 9))] == [0, 3]


def test_decode_keyframes_failures(tmp_path: Path) -> None:
    with pytest.raises(PerceptionError, match="cannot open"):
        decode_keyframes(_chunk(tmp_path / "missing.mp4"), (0,))
    chunk = _chunk(_video(tmp_path / "c.mp4", n_frames=2))
    with pytest.raises(PerceptionError, match="no readable keyframes"):
        decode_keyframes(chunk, (5,))
    assert decode_keyframes(chunk, ()) == []


# --- hosted ------------------------------------------------------------------------------------


def frames_of(chunk: Chunk, indices: tuple[int, ...] = (0, 5, 10)) -> list[Keyframe]:
    return [Keyframe(i, frame_ts(chunk, i), f"jpeg{i}".encode()) for i in indices]


def det(concept: str, object_id: int, box: tuple[float, float, float, float]) -> Detection:
    return Detection(
        camera_id="cam1",
        frame_idx=0,
        ts=datetime(2000, 1, 1, tzinfo=frame_ts(make_chunk(), 0).tzinfo),
        concept=concept,
        object_id=object_id,
        mask=Mask(rle="", box_xyxy=box, confidence=0.9),
    )


class FakeImageSam:
    """Answers `segment_image_bytes` from the image bytes; records every call."""

    def __init__(self, fail_on: bytes | None = None) -> None:
        self.calls: list[tuple[bytes, str, str, str, datetime | None]] = []
        self.fail_on = fail_on
        self._lock = threading.Lock()

    def segment_image_bytes(
        self,
        image: bytes,
        concept: str,
        *,
        content_type: str,
        camera_id: str,
        ts: datetime | None,
    ) -> list[Detection]:
        with self._lock:
            self.calls.append((image, concept, content_type, camera_id, ts))
        if image == self.fail_on:
            raise ModelHTTPError("boom", status=500, attempts=1)
        x = float(image[-1])
        return [det(concept, 1, (x + 10, 0, x + 20, 10)), det(concept, 0, (x, 0, x + 5, 10))]


def test_hosted_keyframes_send_one_request_per_keyframe_and_concept() -> None:
    chunk = make_chunk()
    frames = frames_of(chunk)
    sam = FakeImageSam()
    metrics = PerceptionMetrics()
    stages: list[str] = []
    hosted = HostedKeyframes(
        sam, max_concurrency=3, metrics=metrics, timings=lambda s, *_: stages.append(s)
    )
    assert hosted.billed
    batch = hosted.segment_keyframes(chunk, frames, ("person", "forklift"))
    assert (batch.requests, batch.billed_images, batch.backend) == (6, 6, Backend.HOSTED)
    assert sorted((c[1], c[0]) for c in sam.calls) == sorted(
        (concept, f.jpeg) for concept in ("person", "forklift") for f in frames
    )
    assert {(c[2], c[3]) for c in sam.calls} == {("image/jpeg", "cam1")}
    assert {c[4] for c in sam.calls} == {f.ts for f in frames}
    person = batch.detections["person"]
    assert [(d.frame_idx, d.object_id) for d in person] == [
        (i, o) for i in (0, 5, 10) for o in (0, 1)
    ]
    assert all(d.ts == frame_ts(chunk, d.frame_idx) for d in person)
    assert sorted(stages) == ["sam_queue"] * 6 + ["sam_request"] * 6
    total = metrics.report().total
    assert total.sam_calls_hosted == 6
    hourly = total.hosted_equivalent_usd_per_camera_hour
    assert hourly is None  # no camera time recorded yet
    metrics.record_chunk("cam1", 3600.0)
    assert metrics.report().total.actual_usd_per_camera_hour == pytest.approx(
        6 * HOSTED_USD_PER_IMAGE
    )


def test_hosted_keyframes_raise_the_first_failure() -> None:
    chunk = make_chunk()
    hosted = HostedKeyframes(FakeImageSam(fail_on=b"jpeg5"), max_concurrency=2)
    with pytest.raises(ModelHTTPError):
        hosted.segment_keyframes(chunk, frames_of(chunk), ("person",))


def test_hosted_keyframes_with_no_frames_make_no_request() -> None:
    sam = FakeImageSam()
    batch = HostedKeyframes(sam).segment_keyframes(make_chunk(), [], ("person",))
    assert (batch.requests, batch.detections, sam.calls) == (0, {"person": ()}, [])


# --- self-hosted -------------------------------------------------------------------------------


class FakeImageWorker:
    """Answers `segment_images` with one object per image and concept, or a scripted reply."""

    def __init__(self, reply: dict[str, Any] | None = None) -> None:
        self.reply = reply
        self.calls: list[tuple[list[bytes], dict[str, Any]]] = []

    def segment_images(self, images: list[bytes], request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((images, request))
        if self.reply is not None:
            return self.reply
        objects = [
            {
                "image_idx": i,
                "concept": concept,
                "object_id": 0,
                "rle": "",
                "box_xyxy": [float(i), 0.0, float(i) + 10, 20.0],
                "confidence": 0.8,
            }
            for i in range(len(images))
            for concept in request["concepts"]
        ]
        return {
            "objects": objects,
            "images_processed": len(images),
            "gpu_seconds": 0.2,
            "model_version": "fake",
        }


def test_self_hosted_keyframes_make_one_call_for_every_concept() -> None:
    chunk = make_chunk()
    frames = frames_of(chunk)
    worker = FakeImageWorker()
    bus = RecordingBus()
    metrics = PerceptionMetrics()
    stages: list[str] = []
    hosted = SelfHostedKeyframes(
        worker, bus=bus, metrics=metrics, timings=lambda s, *_: stages.append(s)
    )
    assert not hosted.billed
    batch = hosted.segment_keyframes(chunk, frames, ("person", "forklift"))
    assert (batch.requests, batch.billed_images, batch.backend) == (1, 0, Backend.SELF_HOSTED)
    images, request = worker.calls[0]
    assert images == [f.jpeg for f in frames]
    assert request == {
        "concepts": ["person", "forklift"],
        "n_images": 3,
        "width": 1280,
        "height": 720,
        "include_confidence": True,
        "masks": False,
        "min_confidence": 0.5,
    }
    assert [(d.frame_idx, d.ts) for d in batch.detections["forklift"]] == [
        (f.frame_idx, f.ts) for f in frames
    ]
    assert batch.detections["person"][1].mask.box_xyxy == (1.0, 0.0, 11.0, 20.0)
    assert stages == ["sam_request"]
    assert bus.topics() == [Topic.RELAY_MODEL_USED]
    total = metrics.report().total
    assert (total.sam_calls_self_hosted, total.gpu_seconds) == (1, 0.2)


def test_self_hosted_keyframes_ask_for_the_trackers_low_confidence_floor() -> None:
    chunk = make_chunk()
    worker = FakeImageWorker()
    SelfHostedKeyframes(worker, min_confidence=0.1).segment_keyframes(
        chunk, frames_of(chunk), ("person",)
    )
    assert worker.calls[0][1]["min_confidence"] == 0.1


def test_self_hosted_keyframes_split_long_batches() -> None:
    chunk = make_chunk(fps=30.0, n_frames=90)
    frames = frames_of(chunk, tuple(range(MAX_IMAGES_PER_CALL + 4)))
    worker = FakeImageWorker()
    batch = SelfHostedKeyframes(worker).segment_keyframes(chunk, frames, ("person",))
    assert [len(images) for images, _ in worker.calls] == [MAX_IMAGES_PER_CALL, 4]
    assert batch.requests == 2
    assert [d.frame_idx for d in batch.detections["person"]] == list(range(len(frames)))


@pytest.mark.parametrize(
    "reply",
    [
        {"objects": "nope", "images_processed": 1, "gpu_seconds": 0, "model_version": "x"},
        {
            "objects": [
                {
                    "image_idx": 7,
                    "concept": "person",
                    "object_id": 0,
                    "rle": "",
                    "box_xyxy": [0, 0, 1, 1],
                }
            ],
            "images_processed": 1,
            "gpu_seconds": 0,
            "model_version": "x",
        },
        {
            "objects": [
                {
                    "image_idx": 0,
                    "concept": "cat",
                    "object_id": 0,
                    "rle": "",
                    "box_xyxy": [0, 0, 1, 1],
                }
            ],
            "images_processed": 1,
            "gpu_seconds": 0,
            "model_version": "x",
        },
    ],
)
def test_self_hosted_keyframes_reject_bad_answers(reply: dict[str, Any]) -> None:
    chunk = make_chunk()
    hosted = SelfHostedKeyframes(FakeImageWorker(reply))
    with pytest.raises(SamResponseError):
        hosted.segment_keyframes(chunk, frames_of(chunk, (0,)), ("person",))


@pytest.mark.parametrize(
    "concepts",
    [(), (" ",), ("person", "person")],
)
def test_image_requests_validate_their_concepts(concepts: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="concept"):
        SamImageRequest(concepts=concepts, n_images=1, width=10, height=10)


def test_parse_image_response_accepts_a_valid_answer() -> None:
    request = SamImageRequest(concepts=("person",), n_images=2, width=10, height=10)
    raw = FakeImageWorker().segment_images([b"a", b"b"], request.model_dump(mode="json"))
    response = parse_image_response(raw, request)
    assert [o.image_idx for o in response.objects] == [0, 1]
