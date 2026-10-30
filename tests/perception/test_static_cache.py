from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

import pytest

from rulecam.bus import Bus, Topic
from rulecam.contracts import Chunk, Detection, Mask
from rulecam.perception.static_cache import StaticCache, moved_camera, representatives
from tests.fakes import EPOCH, FakeSegmenter, make_chunk


def _detection(object_id: int, frame_idx: int, confidence: float | None) -> Detection:
    return Detection(
        camera_id="cam1",
        frame_idx=frame_idx,
        ts=EPOCH + timedelta(seconds=frame_idx / 10),
        concept="sink",
        object_id=object_id,
        mask=Mask(rle="9220003", box_xyxy=(2.0, 1.0, 5.0, 3.0), confidence=confidence),
    )


SINK = [_detection(1, 0, 0.7), _detection(1, 1, 0.9), _detection(1, 2, 0.8), _detection(2, 0, None)]


def _cache() -> tuple[StaticCache, FakeSegmenter]:
    segmenter = FakeSegmenter({"sink": SINK})
    return StaticCache(segmenter), segmenter


def test_representatives_keep_the_most_confident_mask_per_object() -> None:
    best = representatives(SINK)
    assert [(d.object_id, d.frame_idx) for d in best] == [(1, 1), (2, 0)]


def test_miss_segments_once_then_hits() -> None:
    cache, segmenter = _cache()
    first = cache.get(make_chunk(index=0), "sink")
    later_chunk = make_chunk(index=7)
    second = cache.get(later_chunk, "sink")

    assert len(segmenter.calls) == 1
    assert len(cache) == 1
    assert [d.object_id for d in second] == [1, 2]
    assert all(d.frame_idx == 0 and d.ts == later_chunk.start_ts for d in second)
    assert [d.mask for d in first] == [d.mask for d in second]


def test_cache_is_per_camera_and_concept() -> None:
    cache, segmenter = _cache()
    cache.get(make_chunk("cam1"), "sink")
    cache.get(make_chunk("cam2"), "sink")
    cache.get(make_chunk("cam1"), "door")
    assert len(segmenter.calls) == 3


def test_new_calibration_is_a_miss() -> None:
    cache, segmenter = _cache()
    cache.get(make_chunk(), "sink", calibration="cal-1")
    cache.get(make_chunk(index=1), "sink", calibration="cal-1")
    cache.get(make_chunk(index=2), "sink", calibration="cal-2")
    assert len(segmenter.calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"camera_id": "cam1", "state": "moved"},
        {"camera_id": "cam1", "state": "blind", "reason": "moved"},
    ],
)
def test_moved_payload_invalidates_only_that_camera(payload: dict[str, str]) -> None:
    cache, segmenter = _cache()
    cache.get(make_chunk("cam1"), "sink")
    cache.get(make_chunk("cam2"), "sink")

    assert cache.handle_coverage_changed(payload)
    cache.get(make_chunk("cam1", index=1), "sink")
    cache.get(make_chunk("cam2", index=1), "sink")

    assert [call.chunk_id for call in segmenter.calls] == [
        "cam1_000000",
        "cam2_000000",
        "cam1_000001",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"camera_id": "cam1", "state": "ok"},
        {"camera_id": "cam1", "state": "blind", "reason": "dark"},
        {"state": "moved"},
        "moved",
        None,
    ],
)
def test_other_coverage_changes_keep_the_cache(payload: object) -> None:
    cache, _ = _cache()
    cache.get(make_chunk(), "sink")
    assert not cache.handle_coverage_changed(payload)
    assert len(cache) == 1


@dataclass(frozen=True)
class _CoverageEvent:
    camera_id: str
    state: str


def test_moved_camera_reads_attributes_too() -> None:
    assert moved_camera(_CoverageEvent("cam3", "MOVED")) == "cam3"


def test_invalidation_during_a_miss_discards_the_stale_result() -> None:
    holder: list[StaticCache] = []

    def responder(chunk: Chunk, concept: str) -> list[Detection]:
        holder[0].invalidate(chunk.camera_id)  # the camera moves while SAM is running
        return SINK

    cache = StaticCache(FakeSegmenter(responder=responder))
    holder.append(cache)
    assert len(cache.get(make_chunk(), "sink")) == 2
    assert len(cache) == 0


async def test_run_invalidates_on_the_bus_event() -> None:
    bus = Bus()
    cache, segmenter = _cache()
    cache.get(make_chunk(), "sink")
    listener = asyncio.create_task(cache.run(bus))
    await asyncio.sleep(0)
    assert bus.subscriber_count(Topic.COVERAGE_CHANGED) == 1

    bus.publish(Topic.COVERAGE_CHANGED, {"camera_id": "cam1", "state": "moved"})
    for _ in range(10):
        await asyncio.sleep(0)
        if len(cache) == 0:
            break

    assert len(cache) == 0
    listener.cancel()
    with pytest.raises(asyncio.CancelledError):
        await listener
    assert bus.subscriber_count(Topic.COVERAGE_CHANGED) == 0
    cache.get(make_chunk(index=1), "sink")
    assert len(segmenter.calls) == 2
