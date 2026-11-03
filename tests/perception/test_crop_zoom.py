"""Crop-zoom SAM 3.1 (add-on 3.3): crop geometry, the GPU engine, the route and the clients."""

from __future__ import annotations

import base64
from collections.abc import Iterator, Sequence
from typing import Any

import cv2
import httpx
import numpy as np
import pytest

from rulecam.clients.transport import HttpxTransport
from rulecam.contracts.core import BoxXYXY, Detection, Keypoint, Mask
from rulecam.perception.crop_worker import (
    CROP_MODEL_VERSION,
    CropEngine,
    check_crops_request,
    crop_pixels,
)
from rulecam.perception.crop_zoom import (
    MIN_SIDE_PX,
    CropHit,
    FallbackCrops,
    LocalCrops,
    WorkerCrops,
    body_crop,
    encode_jpeg,
    hand_crops,
    head_crop,
    parse_crops_response,
    person_crops,
)
from rulecam.perception.errors import SamResponseError, SamRuntimeUnavailableError
from rulecam.perception.sam_runtime import HttpSamWorker
from rulecam.perception.sam_server import SEGMENT_CROPS_PATH, parse_image_body
from tests.fakes import EPOCH
from tests.perception.test_sam_server import KEY, FakeEngine, Running

PERSON: BoxXYXY = (100.0, 100.0, 160.0, 300.0)  # 60 x 200 px, in a 640 x 480 frame
W, H = 640, 480


def keypoints(**known: tuple[float, float]) -> tuple[Keypoint, ...]:
    """COCO-17 keypoints, all unknown except the named ones (`k0` .. `k16`)."""
    return tuple(
        Keypoint(x=known[f"k{k}"][0], y=known[f"k{k}"][1], score=0.9)
        if f"k{k}" in known
        else Keypoint(x=0.0, y=0.0, score=0.05)
        for k in range(17)
    )


# --- crop geometry ----------------------------------------------------------------------------


def test_head_crop_without_pose_covers_the_top_of_the_box_and_above() -> None:
    x0, y0, x1, y1 = head_crop(PERSON, W, H)  # type: ignore[misc]
    assert (x0 + x1) / 2 == pytest.approx(130.0)
    assert y0 < PERSON[1] < y1  # room above the head for a hat or hair net
    assert y1 < PERSON[1] + 0.35 * 200
    assert x1 - x0 == pytest.approx(0.34 * 200)


def test_head_crop_follows_the_face_keypoints() -> None:
    x0, y0, x1, y1 = head_crop(PERSON, W, H, keypoints(k0=(150.0, 130.0), k1=(152.0, 128.0)))  # type: ignore[misc]
    assert (x0 + x1) / 2 == pytest.approx(151.0)
    assert y0 < 129.0 < y1


def test_hand_crops_use_known_wrists_else_the_arm_band() -> None:
    wrists = hand_crops(PERSON, W, H, keypoints(k9=(90.0, 220.0), k10=(170.0, 230.0)))
    assert len(wrists) == 2
    assert all(b[0] < cx < b[2] for b, cx in zip(wrists, (90.0, 170.0), strict=True))
    (band,) = hand_crops(PERSON, W, H)
    assert band[0] < PERSON[0]
    assert band[2] > PERSON[2]
    assert band[1] == pytest.approx(160.0)
    assert band[3] == pytest.approx(270.0)


def test_small_people_get_crops_of_at_least_the_minimum_side_and_all_are_clipped() -> None:
    tiny: BoxXYXY = (2.0, 2.0, 8.0, 22.0)
    x0, y0, x1, _y1 = head_crop(tiny, W, H)  # type: ignore[misc]
    assert (x0, y0) == (0.0, 0.0)  # clipped to the frame
    assert x1 == pytest.approx(5.0 + MIN_SIDE_PX / 2)
    assert body_crop((600.0, 400.0, 660.0, 500.0), W, H) == pytest.approx((594.0, 390.0, W, H))


def test_person_crops_per_region_in_order_and_none_for_an_empty_box() -> None:
    crops = person_crops(
        PERSON, regions=("hands", "head", "hands"), width=W, height=H, person_idx=3
    )
    assert [c.region for c in crops] == ["hands", "head"]
    assert {c.person_idx for c in crops} == {3}
    assert person_crops((5.0, 5.0, 5.0, 9.0), regions=("head",), width=W, height=H) == []


# --- the GPU engine (`crop_worker`) ------------------------------------------------------------


class FakePredictor:
    """Finds one object in the middle of every crop, with a score per concept."""

    def __init__(self) -> None:
        self.shapes: list[tuple[int, ...]] = []

    def set_image(self, image: np.ndarray) -> Any:
        self.shapes.append(image.shape)
        return image.shape

    def detect(self, state: Any, concept: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        height, width = state[:2]
        boxes = np.array([[width / 4, height / 4, 3 * width / 4, 3 * height / 4]], np.float32)
        score = 0.42 if concept == "hair net" else 0.9
        return boxes, np.array([score], np.float32), np.zeros((1, height, width), bool)


class Memory:
    def __init__(self) -> None:
        self.released = 0

    def stats(self) -> dict[str, int]:
        return {}

    def release(self) -> None:
        self.released += 1

    def reset_peak(self) -> None:
        return None


def test_crop_engine_runs_each_crop_and_answers_in_frame_pixels() -> None:
    predictor, memory = FakePredictor(), Memory()
    engine = CropEngine(
        predictor, memory=memory, decode=lambda _: np.zeros((480, 640, 3), np.uint8)
    )
    out = engine.segment_crops(
        b"jpeg",
        {
            "concepts": ["hair net", "glove"],
            "crops": [[100, 80, 180, 160], [635, 475, 700, 520], [0, 0, 40, 20]],
            "include_confidence": True,
        },
    )
    assert out["model_version"] == CROP_MODEL_VERSION
    assert out["crops_processed"] == 2  # the second box has 5 x 5 px inside: too small
    assert predictor.shapes == [(80, 80, 3), (20, 40, 3)]
    first = [o for o in out["objects"] if o["crop_idx"] == 0]
    assert [o["concept"] for o in first] == ["hair net", "glove"]
    assert first[0]["box_xyxy"] == pytest.approx([120.0, 100.0, 160.0, 140.0])
    assert first[0]["confidence"] == pytest.approx(0.42)
    assert memory.released == 1
    assert engine.memory_stats() == {"calls": 1, "crops": 2}


def test_crop_engine_releases_memory_when_a_call_fails() -> None:
    memory = Memory()

    def broken(_: bytes) -> np.ndarray:
        return np.zeros((10, 10, 3), np.uint8)

    class Failing(FakePredictor):
        def set_image(self, image: np.ndarray) -> Any:
            raise RuntimeError("cuda")

    engine = CropEngine(Failing(), memory=memory, decode=broken)
    with pytest.raises(RuntimeError):
        engine.segment_crops(b"x", {"concepts": ["hat"], "crops": [[0, 0, 10, 10]]})
    assert memory.released == 1


@pytest.mark.parametrize(
    "request_",
    [
        {"concepts": [], "crops": [[0, 0, 1, 1]]},
        {"concepts": [" "], "crops": [[0, 0, 1, 1]]},
        {"concepts": ["hat"], "crops": []},
        {"concepts": ["hat"], "crops": [[0, 0, 1]]},
        {"concepts": ["hat"], "crops": [[0, 0, "a", 1]]},
        {"concepts": ["hat"], "crops": [[5, 0, 1, 1]]},
        {"concepts": "hat", "crops": [[0, 0, 1, 1]]},
    ],
)
def test_malformed_crop_requests_are_refused(request_: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"concept|crop"):
        check_crops_request(request_)


def test_crop_pixels_clips_and_refuses_slivers() -> None:
    image = np.arange(100 * 100 * 3, dtype=np.uint8).reshape(100, 100, 3)
    cut = crop_pixels(image, (-5.0, 90.0, 20.0, 120.0))
    assert cut is not None
    assert cut[0].shape == (10, 20, 3)
    assert cut[1] == (0, 90)
    assert crop_pixels(image, (95.0, 0.0, 120.0, 50.0)) is None


# --- the route and the HTTP client -------------------------------------------------------------


class FakeCropEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, dict[str, Any]]] = []

    def segment_crops(self, image: bytes, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((image, request))
        return {
            "objects": [
                {"crop_idx": 0, "concept": request["concepts"][0], "box_xyxy": [1, 2, 3, 4]}
            ],
            "crops_processed": len(request["crops"]),
            "gpu_seconds": 0.1,
            "model_version": "fake-crops",
        }

    def memory_stats(self) -> dict[str, Any]:
        return {"calls": len(self.calls), "crops": 0}


@pytest.fixture
def cropping() -> Iterator[Running]:
    server = Running(FakeEngine(), crop_engine=FakeCropEngine())
    yield server
    server.close()


def test_segment_crops_round_trip_through_the_http_worker(cropping: Running) -> None:
    worker = HttpSamWorker(HttpxTransport(cropping.url, KEY))
    hits = WorkerCrops(worker).segment_crops(
        b"\xff\xd8frame", [(0.0, 0.0, 50.0, 50.0)], ["hair net"], camera_id="cam1"
    )
    assert hits == [CropHit(crop_idx=0, concept="hair net", box_xyxy=(1.0, 2.0, 3.0, 4.0))]
    image, request = cropping.server.crop_engine.calls[0]  # type: ignore[union-attr]
    assert image == b"\xff\xd8frame"
    assert request == {
        "concepts": ["hair net"],
        "crops": [[0.0, 0.0, 50.0, 50.0]],
        "include_confidence": True,
    }
    health = httpx.get(f"{cropping.url}/health").json()
    assert health["crops"] == {"loaded": True, "waiting": 0, "calls": 1, "crops": 0}


@pytest.mark.parametrize(
    "body",
    [
        {"request": {"concepts": ["hat"], "crops": [[0, 0, 1, 1]]}},
        {"request": {"concepts": ["hat"], "crops": []}, "image_b64": "aGk="},
        {"request": {"concepts": ["hat"], "crops": [[0, 0, 1, 1]]}, "image_b64": "!!"},
    ],
)
def test_segment_crops_malformed_bodies_are_400(cropping: Running, body: dict[str, Any]) -> None:
    reply = httpx.post(
        f"{cropping.url}{SEGMENT_CROPS_PATH}", json=body, headers={"Authorization": f"Bearer {KEY}"}
    )
    assert reply.status_code == 400


def test_segment_crops_is_404_without_an_engine_and_needs_the_key() -> None:
    server = Running(FakeEngine())
    try:
        body = {"request": {"concepts": ["hat"], "crops": [[0, 0, 1, 1]]}, "image_b64": "aGk="}
        url = f"{server.url}{SEGMENT_CROPS_PATH}"
        assert (
            httpx.post(url, json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
        )
        ok = httpx.post(url, json=body, headers={"Authorization": f"Bearer {KEY}"})
        assert ok.status_code == 404
        assert httpx.get(f"{server.url}/health").json()["crops"] == {"loaded": False}
    finally:
        server.close()


def test_parse_image_body() -> None:
    image, request = parse_image_body(
        b'{"request": {"a": 1}, "image_b64": "' + base64.b64encode(b"img") + b'"}'
    )
    assert (image, request) == (b"img", {"a": 1})


def test_worker_answers_naming_unknown_crops_or_concepts_are_refused() -> None:
    raw = {"objects": [{"crop_idx": 2, "concept": "hat", "box_xyxy": [0, 0, 1, 1]}]}
    with pytest.raises(SamResponseError):
        parse_crops_response(
            {**raw, "crops_processed": 1, "gpu_seconds": 0, "model_version": "x"}, 1, ["hat"]
        )
    with pytest.raises(SamResponseError):
        parse_crops_response({"objects": "nope"}, 1, ["hat"])
    assert WorkerCrops(FakeCropEngine()).segment_crops(b"", [], ["hat"], camera_id="c") == []


# --- hosted SAM on local crops, and the fallback ---------------------------------------------


class FakeBytesSegmenter:
    """Finds one item at (5, 5)-(15, 15) of every crop it is sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[int, ...], str, str]] = []

    def segment_image_bytes(
        self,
        image: bytes,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        pixels = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        assert pixels is not None
        self.calls.append((pixels.shape, concept, camera_id))
        mask = Mask(rle="", box_xyxy=(5.0, 5.0, 15.0, 15.0), confidence=0.6)
        return [
            Detection(
                camera_id=camera_id, frame_idx=0, ts=EPOCH, concept=concept, object_id=0, mask=mask
            )
        ]


def test_local_crops_cut_here_and_map_boxes_back_to_the_frame() -> None:
    frame = encode_jpeg(np.full((H, W, 3), 90, np.uint8))
    segmenter = FakeBytesSegmenter()
    hits = LocalCrops(segmenter).segment_crops(
        frame, [(100.0, 50.0, 180.0, 130.0), (639.5, 0.0, 700.0, 10.0)], ["hat"], camera_id="cam9"
    )
    assert segmenter.calls == [((80, 80, 3), "hat", "cam9")]  # the sliver is skipped
    assert hits == [
        CropHit(crop_idx=0, concept="hat", box_xyxy=(105.0, 55.0, 115.0, 65.0), confidence=0.6)
    ]
    with pytest.raises(ValueError, match="decoded"):
        LocalCrops(segmenter).segment_crops(b"not an image", [(0, 0, 9, 9)], ["hat"], camera_id="c")


class Broken:
    def segment_crops(
        self, image: bytes, crops: Sequence[BoxXYXY], concepts: Sequence[str], *, camera_id: str
    ) -> list[CropHit]:
        raise SamRuntimeUnavailableError("pod asleep")


def test_fallback_crops_use_the_second_segmenter_when_the_pod_fails() -> None:
    frame = encode_jpeg(np.full((H, W, 3), 90, np.uint8))
    hits = FallbackCrops(Broken(), LocalCrops(FakeBytesSegmenter())).segment_crops(
        frame, [(0.0, 0.0, 40.0, 40.0)], ["glove"], camera_id="cam1"
    )
    assert [h.concept for h in hits] == ["glove"]
