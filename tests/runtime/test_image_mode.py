"""End to end: the image-mode runtime finds the same candidates as video mode (WS2b, P3-08).

One synthetic scene, segmented two ways: video mode gets every frame of each chunk with SAM's
per-chunk object ids (`Tracker`, IoU hand-off); image mode gets only the 2 keyframes per second,
with per-image object numbers in a different order (`KeyframeTracker`, ByteTrack). Both run the
same loop, rules engine and evidence chain.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from rulecam.contracts import CandidateEvent, Chunk, Detection, Mask
from rulecam.perception.keyframe_tracker import KeyframeTracker
from rulecam.perception.keyframes import Keyframe, KeyframeBatch, frame_ts
from rulecam.perception.metrics import Backend
from rulecam.perception.tracking import Tracker
from rulecam.runtime.loop import ActivePlan, ChunkTracker
from tests.fakes import EPOCH, FakeClock, FakeSegmenter, InMemoryTrackStore, make_chunk
from tests.rules.helpers import CAL, PX_PER_M, make_rule
from tests.runtime.test_loop import at_end_of

NEAR_A_MOVING_FORKLIFT = make_rule(
    {
        "op": "all",
        "of": [
            {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0},
            {"op": "moving", "object": "forklift", "speed_mps_gt": 0.2},
        ],
    },
    concepts={"person": "person", "forklift": "forklift"},
    rule_id="forklift-proximity",
)
CHUNKS = 4  # 12 s


def foot_m(concept: str, slot: int, t: float) -> tuple[float, float]:
    """The scene: a forklift driving past a standing pedestrian; a bystander far away."""
    if concept == "forklift":
        return (0.5 + 1.0 * t, 3.0)  # 1 m/s past the pedestrian; footprints within 2 m mid-way
    return ((4.0, 4.0), (9.0, 9.0))[slot]


def box(concept: str, slot: int, t: float) -> tuple[float, float, float, float]:
    x, y = (v * PX_PER_M for v in foot_m(concept, slot, t))
    half_w, h = (60.0, 100.0) if concept == "forklift" else (25.0, 170.0)
    return (x - half_w, y - h, x + half_w, y)


def objects(concept: str) -> range:
    return range(1 if concept == "forklift" else 2)


def detection(chunk: Chunk, concept: str, slot: int, frame_idx: int, object_id: int) -> Detection:
    t = (frame_ts(chunk, frame_idx) - EPOCH).total_seconds()
    return Detection(
        camera_id=chunk.camera_id,
        frame_idx=frame_idx,
        ts=frame_ts(chunk, frame_idx),
        concept=concept,
        object_id=object_id,
        mask=Mask(rle="", box_xyxy=box(concept, slot, t), confidence=0.9),
    )


def video_world(chunk: Chunk, concept: str) -> list[Detection]:
    """SAM 3.1 video: every frame, object ids stable within the chunk."""
    return [
        detection(chunk, concept, slot, i, slot + 1)
        for i in range(chunk.n_frames)
        for slot in objects(concept)
    ]


class ImageWorld:
    """SAM 3.1 image mode on keyframes: object numbers per image, in reverse order."""

    billed = True

    def segment_keyframes(
        self, chunk: Chunk, frames: Sequence[Keyframe], concepts: Sequence[str]
    ) -> KeyframeBatch:
        found = {
            concept: tuple(
                detection(chunk, concept, slot, frame.frame_idx, n)
                for frame in frames
                for n, slot in enumerate(reversed(objects(concept)))
            )
            for concept in concepts
        }
        n = len(frames) * len(concepts)
        return KeyframeBatch(detections=found, requests=n, billed_images=n, backend=Backend.HOSTED)


def decode(chunk: Chunk, indices: Sequence[int]) -> list[Keyframe]:
    return [Keyframe(i, frame_ts(chunk, i), b"jpeg") for i in indices]


def run(tmp_path: Path, tracker: ChunkTracker, store: InMemoryTrackStore) -> list[CandidateEvent]:
    from tests.runtime.fakes import make_loop  # noqa: PLC0415

    clock = FakeClock()
    loop, _, _, _ = make_loop(tmp_path, clock, tracker=tracker, store=store)
    loop.set_plan(ActivePlan(rules=(NEAR_A_MOVING_FORKLIFT,), calibration=CAL))
    found: list[CandidateEvent] = []
    for index in range(CHUNKS):
        at_end_of(clock, index)
        found += loop.handle_chunk(make_chunk(index=index))
    return found


@pytest.fixture
def video(tmp_path: Path) -> tuple[list[CandidateEvent], InMemoryTrackStore]:
    store = InMemoryTrackStore()
    tracker = Tracker(FakeSegmenter(responder=video_world), store=store, reprompt=False)
    return run(tmp_path / "video", tracker, store), store


@pytest.fixture
def image(tmp_path: Path) -> tuple[list[CandidateEvent], InMemoryTrackStore]:
    store = InMemoryTrackStore()
    tracker = KeyframeTracker(ImageWorld(), store=store, decoder=decode)
    return run(tmp_path / "image", tracker, store), store


def test_image_mode_finds_the_same_candidates_as_video_mode(
    video: tuple[list[CandidateEvent], InMemoryTrackStore],
    image: tuple[list[CandidateEvent], InMemoryTrackStore],
) -> None:
    by_video, by_image = video[0], image[0]
    assert [c.rule_id for c in by_video] == ["forklift-proximity"]
    assert [c.rule_id for c in by_image] == [c.rule_id for c in by_video]
    seen, expected = by_image[0], by_video[0]
    tolerance = timedelta(seconds=0.5)  # one keyframe interval
    assert abs(seen.ts_start - expected.ts_start) <= tolerance
    assert abs(seen.ts_end - expected.ts_end) <= tolerance
    assert timedelta(0) < seen.ts_start - EPOCH < timedelta(seconds=3)  # the approach, not t=0
    assert (seen.severity, seen.camera_id) == (expected.severity, expected.camera_id)


def test_image_mode_tracks_are_continuous_with_one_id_per_object(
    video: tuple[list[CandidateEvent], InMemoryTrackStore],
    image: tuple[list[CandidateEvent], InMemoryTrackStore],
) -> None:
    end = EPOCH + timedelta(seconds=3 * CHUNKS)
    video_ids = {t.track_id for t in video[1].window("cam1", EPOCH, end)}
    image_tracks = image[1].window("cam1", EPOCH, end)
    assert len({t.track_id for t in image_tracks}) == len(video_ids) == 3
    points = sorted(
        (p.ts for t in image_tracks if t.concept == "forklift" for p in t.points),
    )
    assert len(points) == 2 * 3 * CHUNKS  # every keyframe, nothing lost at chunk borders
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(points)]
    assert max(gaps) == pytest.approx(0.5)


def test_hosted_keyframes_bill_the_budget_in_video_frame_units(tmp_path: Path) -> None:
    """2 concepts x 6 keyframes x 12.5 frames per image = 150 billed frames per chunk."""
    from rulecam.runtime.sampling import SamplingPolicy  # noqa: PLC0415
    from tests.runtime.fakes import make_loop  # noqa: PLC0415

    store = InMemoryTrackStore()
    tracker = KeyframeTracker(ImageWorld(), store=store, decoder=decode)
    estimates: list[tuple[str, int]] = []

    def estimate(chunk: Chunk, concepts: int) -> int:
        estimates.append((chunk.chunk_id, concepts))
        return tracker.estimate(chunk, concepts)

    clock = FakeClock()
    loop, _, _, _ = make_loop(
        tmp_path,
        clock,
        tracker=tracker,
        store=store,
        usage=tracker,
        bill_estimate=estimate,
        sampling=SamplingPolicy(budget_frames_per_min=200, budget_stride=3),
    )
    loop.set_plan(ActivePlan(rules=(NEAR_A_MOVING_FORKLIFT,), calibration=CAL))
    for index in range(3):
        at_end_of(clock, index)
        loop.handle_chunk(make_chunk(index=index))
    metrics = loop.metrics
    assert estimates == [("cam1_000000", 2), ("cam1_000001", 2)]
    assert (metrics.sam_calls, metrics.billed_frames) == (24, 300)
    assert metrics.chunks_skipped_budget == 1  # 300 >= 200 in the last minute: degraded
