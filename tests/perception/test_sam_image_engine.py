"""The GPU side of image mode, `Sam31ImageEngine`, with a fake detector (WS2c; no GPU)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from typing import Any

import cv2
import numpy as np
import pytest

from rulecam.perception.masks import NO_MASK, decode_rle
from rulecam.perception.sam_runtime import SamImageRequest, parse_image_response
from rulecam.perception.sam_worker import (
    IMAGE_MODEL_VERSION,
    BatchImagePredictor,
    BoxEmbedder,
    ImagePredictor,
    Sam31ImageEngine,
    decode_jpeg,
    image_objects,
)
from tests.perception.test_sam_worker import FakeMemory

H, W = 6, 8


def mask(x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    out = np.zeros((H, W), dtype=bool)
    out[y0:y1, x0:x1] = True
    return out


class FakeImagePredictor:
    """Answers each concept with preset objects; records encodes and detections."""

    def __init__(
        self, by_concept: dict[str, list[tuple[float, tuple[int, int, int, int]]]]
    ) -> None:
        self.by_concept = by_concept
        self.encoded: list[tuple[int, ...]] = []
        self.detected: list[tuple[int, str]] = []

    def set_image(self, image: np.ndarray) -> Any:
        self.encoded.append(image.shape)
        return len(self.encoded) - 1

    def detect(self, state: Any, concept: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        self.detected.append((state, concept))
        objects = self.by_concept.get(concept, [])
        boxes = np.array([[float(v) for v in box] for _, box in objects]).reshape(-1, 4)
        scores = np.array([score for score, _ in objects], dtype=float)
        masks = (
            np.stack([mask(*box) for _, box in objects])
            if objects
            else np.zeros((0, H, W), dtype=bool)
        )
        return boxes, scores, masks


def jpeg(value: int = 100) -> bytes:
    ok, data = cv2.imencode(".jpg", np.full((H, W, 3), value, dtype=np.uint8))
    assert ok
    return data.tobytes()


def request(**overrides: Any) -> dict[str, Any]:
    base = SamImageRequest(concepts=("person", "forklift"), n_images=2, width=W, height=H)
    return {**base.model_dump(mode="json"), **overrides}


def test_each_image_is_encoded_once_and_every_concept_detected_on_it() -> None:
    predictor = FakeImagePredictor(
        {"person": [(0.6, (0, 0, 2, 3)), (0.9, (4, 1, 7, 5))], "forklift": []}
    )
    engine = Sam31ImageEngine(predictor)
    reply = engine.segment_images([jpeg(), jpeg(50)], request())
    assert predictor.encoded == [(H, W, 3), (H, W, 3)]
    assert predictor.detected == [(0, "person"), (0, "forklift"), (1, "person"), (1, "forklift")]
    assert reply["images_processed"] == 2
    assert reply["model_version"] == IMAGE_MODEL_VERSION
    objects = reply["objects"]
    assert [(o["image_idx"], o["object_id"], o["confidence"]) for o in objects] == [
        (0, 0, 0.9),
        (0, 1, 0.6),
        (1, 0, 0.9),
        (1, 1, 0.6),
    ]  # best score first
    assert {o["rle"] for o in objects} == {NO_MASK}  # boxes only by default
    response = parse_image_response(reply, SamImageRequest.model_validate(request()))
    assert response.objects[0].box_xyxy == (4.0, 1.0, 7.0, 5.0)


def test_requests_cut_at_their_min_confidence_and_old_requests_at_one_half() -> None:
    predictor = FakeImagePredictor({"person": [(0.9, (0, 0, 2, 3)), (0.3, (4, 1, 7, 5))]})
    engine = Sam31ImageEngine(predictor)
    low = engine.segment_images(
        [jpeg()], request(concepts=["person"], n_images=1, min_confidence=0.1)
    )
    assert [o["confidence"] for o in low["objects"]] == [0.9, 0.3]
    legacy = {
        k: v for k, v in request(concepts=["person"], n_images=1).items() if k != "min_confidence"
    }
    assert [o["confidence"] for o in engine.segment_images([jpeg()], legacy)["objects"]] == [0.9]
    default = engine.segment_images([jpeg()], request(concepts=["person"], n_images=1))
    assert [o["object_id"] for o in default["objects"]] == [0]


class FakeReid:
    """Embeds each box as `[x0, y0]` normalised, records the batches."""

    model_name = "fake-osnet"

    def __init__(self) -> None:
        self.calls: list[list[tuple[int, ...]]] = []

    def embed_boxes(
        self, images: Sequence[np.ndarray], boxes: Sequence[np.ndarray]
    ) -> list[np.ndarray]:
        self.calls.append([b.shape for b in boxes])
        assert all(image.shape == (H, W, 3) for image in images)
        return [np.array([[x0 + 1.0, y0] for x0, y0, *_ in found]) for found in boxes]


def test_embed_concepts_get_reid_vectors_in_one_batch() -> None:
    predictor = FakeImagePredictor(
        {"person": [(0.9, (0, 0, 2, 3)), (0.3, (4, 1, 7, 5))], "forklift": [(0.8, (1, 1, 3, 3))]}
    )
    reid = FakeReid()
    engine = Sam31ImageEngine(predictor, reid=reid)
    reply = engine.segment_images(
        [jpeg(), jpeg(50)], request(min_confidence=0.1, embed_concepts=["person"])
    )
    assert reid.calls == [[(2, 4), (2, 4)]]  # one call: every image's people
    people = [o for o in reply["objects"] if o["concept"] == "person"]
    assert [o["embedding"] for o in people[:2]] == [[1.0, 0.0], [5.0, 1.0]]
    assert all("embedding" not in o for o in reply["objects"] if o["concept"] == "forklift")
    assert engine.memory_stats()["reid"] == "fake-osnet"
    response = parse_image_response(reply, SamImageRequest.model_validate(request()))
    assert response.objects[0].embedding == (1.0, 0.0)


def test_no_vectors_without_a_reid_model_or_embed_concepts() -> None:
    predictor = FakeImagePredictor({"person": [(0.9, (0, 0, 2, 3))]})
    plain = Sam31ImageEngine(predictor).segment_images(
        [jpeg()], request(concepts=["person"], n_images=1, embed_concepts=["person"])
    )
    reid = FakeReid()
    unasked = Sam31ImageEngine(predictor, reid=reid).segment_images(
        [jpeg()], request(concepts=["person"], n_images=1)
    )
    assert all("embedding" not in o for o in (*plain["objects"], *unasked["objects"]))
    assert reid.calls == []


def test_fake_reid_satisfies_the_protocol() -> None:
    reid: BoxEmbedder = FakeReid()
    assert reid.model_name == "fake-osnet"


class FakeBatchPredictor(FakeImagePredictor):
    """Also encodes several images at once; records the batch sizes."""

    def __init__(
        self, by_concept: dict[str, list[tuple[float, tuple[int, int, int, int]]]]
    ) -> None:
        super().__init__(by_concept)
        self.batches: list[int] = []

    def set_images(self, images: Sequence[np.ndarray]) -> list[Any]:
        self.batches.append(len(images))
        return [self.set_image(image) for image in images]


def test_batched_encoding_gives_the_same_answer_in_fewer_backbone_passes() -> None:
    found = {
        "person": [(0.6, (0, 0, 2, 3)), (0.9, (4, 1, 7, 5))],
        "forklift": [(0.7, (1, 1, 3, 3))],
    }
    images = [jpeg(v) for v in (10, 60, 110, 160, 210)]
    single = Sam31ImageEngine(FakeImagePredictor(found)).segment_images(images, request(n_images=5))
    predictor = FakeBatchPredictor(found)
    engine = Sam31ImageEngine(predictor, batch_size=2)
    batched = engine.segment_images(images, request(n_images=5))
    assert predictor.batches == [2, 2, 1]
    assert batched["objects"] == single["objects"]
    assert engine.memory_stats()["batch"] == 2


def test_batching_is_off_by_default_and_needs_a_batch_predictor() -> None:
    predictor = FakeBatchPredictor({"person": [(0.9, (0, 0, 2, 3))]})
    Sam31ImageEngine(predictor).segment_images([jpeg(), jpeg()], request())
    assert predictor.batches == []
    plain = FakeImagePredictor({"person": [(0.9, (0, 0, 2, 3))]})
    reply = Sam31ImageEngine(plain, batch_size=4).segment_images([jpeg(), jpeg()], request())
    assert reply["images_processed"] == 2
    assert not isinstance(plain, BatchImagePredictor)
    assert isinstance(predictor, BatchImagePredictor)


def test_masks_and_confidences_on_request() -> None:
    predictor = FakeImagePredictor({"person": [(0.7, (1, 1, 3, 4))]})
    reply = Sam31ImageEngine(predictor).segment_images(
        [jpeg()], request(concepts=["person"], n_images=1, masks=True, include_confidence=False)
    )
    (obj,) = reply["objects"]
    assert obj["confidence"] is None
    assert decode_rle(obj["rle"]).sum() == 6


def test_boxes_are_clipped_to_the_image() -> None:
    boxes = np.array([[-3.0, -1.0, 20.0, 9.0]])
    found = (boxes, np.array([0.8]), np.zeros((1, H, W), dtype=bool))
    (obj,) = image_objects(0, "person", found, masks=False, include_confidence=True)
    assert obj["box_xyxy"] == [0.0, 0.0, float(W), float(H)]


def test_inference_context_and_memory_release_around_every_call() -> None:
    entered: list[str] = []

    @contextlib.contextmanager
    def context() -> Iterator[None]:
        entered.append("in")
        yield
        entered.append("out")

    memory = FakeMemory()
    engine = Sam31ImageEngine(FakeImagePredictor({}), context, memory=memory)
    engine.segment_images([jpeg()], request(n_images=1))
    assert entered == ["in", "out"]
    assert memory.calls == ["release", "stats"]
    assert engine.memory_stats() == {"calls": 1, "guard_trips": 0, "reid": None, "batch": 1}


def test_memory_guard_frees_again_and_counts() -> None:
    memory = FakeMemory([30 * 2**30])
    engine = Sam31ImageEngine(FakeImagePredictor({}), memory=memory, memory_guard_bytes=2**30)
    engine.segment_images([jpeg()], request(n_images=1))
    assert memory.calls.count("release") == 2
    assert engine.memory_stats()["guard_trips"] == 1


def test_a_bad_image_fails_the_call_and_still_releases_memory() -> None:
    memory = FakeMemory()
    engine = Sam31ImageEngine(FakeImagePredictor({}), memory=memory)
    with pytest.raises(ValueError, match="could not be decoded"):
        engine.segment_images([b"not an image"], request(n_images=1))
    assert memory.calls[0] == "release"
    assert engine.memory_stats()["calls"] == 1


def test_decode_jpeg_gives_rgb() -> None:
    ok, data = cv2.imencode(".png", np.dstack([np.full((H, W), v, np.uint8) for v in (10, 20, 30)]))
    assert ok
    rgb = decode_jpeg(data.tobytes())
    assert rgb.shape == (H, W, 3)
    assert tuple(rgb[0, 0]) == (30, 20, 10)  # OpenCV's BGR flipped to RGB


def test_fake_predictor_satisfies_the_protocol() -> None:
    predictor: ImagePredictor = FakeImagePredictor({})
    assert predictor.detect(predictor.set_image(np.zeros((H, W, 3), np.uint8)), "x")[1].size == 0
