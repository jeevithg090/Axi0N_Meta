"""Image-mode tracking: ByteTrack identities over SAM keyframes, static cache, cost (WS2b)."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta

import numpy as np
import pytest

from rulecam.bus import Topic
from rulecam.contracts import Chunk, Detection, Mask
from rulecam.perception.keyframe_tracker import (
    IMAGE_FRAME_EQUIVALENT,
    LOST_TRACK_S,
    KeyframeIdentities,
    KeyframeTracker,
    new_bytetrack,
)
from rulecam.perception.keyframes import (
    FrameDecoder,
    Keyframe,
    KeyframeBatch,
    KeyframeRates,
    frame_ts,
    keyframe_indices,
)
from rulecam.perception.metrics import Backend
from rulecam.rules import evaluate
from rulecam.runtime.timings import StageTimings
from tests.fakes import EPOCH, InMemoryTrackStore, make_chunk
from tests.rules.helpers import CAL, make_rule
from tests.runtime.fakes import RecordingBus

Box = tuple[float, float, float, float]
BoxAt = Callable[[Chunk, int], Box]
World = Mapping[str, Sequence[BoxAt]]


def suffixes() -> Callable[[], str]:
    numbers = itertools.count(1)
    return lambda: f"{next(numbers):08x}"


def det(
    chunk: Chunk, frame_idx: int, box: Box, *, object_id: int = 0, conf: float = 0.9
) -> Detection:
    return Detection(
        camera_id=chunk.camera_id,
        frame_idx=frame_idx,
        ts=frame_ts(chunk, frame_idx),
        concept="person",
        object_id=object_id,
        mask=Mask(rle="", box_xyxy=box, confidence=conf),
    )


def keyframes(chunk: Chunk, rate: float = 2.0) -> list[tuple[int, datetime]]:
    return [(i, frame_ts(chunk, i)) for i in keyframe_indices(chunk.n_frames, chunk.fps, rate)]


def walker(x0: float, speed_px_s: float, y: float = 100.0) -> BoxAt:
    """A person box whose left edge is at `x0 + speed * t` (t from EPOCH)."""

    def box_at(chunk: Chunk, frame_idx: int) -> Box:
        t = (frame_ts(chunk, frame_idx) - EPOCH).total_seconds()
        x = x0 + speed_px_s * t
        return (x, y, x + 40.0, y + 80.0)

    return box_at


def ids_of(assigned: dict[str, list[Detection]]) -> set[str]:
    return set(assigned)


# --- identities -------------------------------------------------------------------------------


def test_new_objects_are_named_on_their_first_keyframe() -> None:
    identities = KeyframeIdentities(new_suffix=suffixes())
    chunk = make_chunk()
    first = [det(chunk, 0, (0, 0, 40, 80)), det(chunk, 0, (300, 0, 340, 80), object_id=1)]
    assigned = identities.assign("cam1", "person", keyframes(chunk)[:1], first)
    assert sorted(assigned) == ["cam1:person:00000001", "cam1:person:00000002"]
    assert all(len(dets) == 1 for dets in assigned.values())


def test_identity_survives_keyframes_and_chunk_borders() -> None:
    identities = KeyframeIdentities()
    box_at = walker(0.0, 60.0)  # 30 px between 2 Hz keyframes; the box is 40 px wide
    seen: list[set[str]] = []
    for index in range(4):
        chunk = make_chunk(index=index)
        frames = keyframes(chunk)
        dets = [det(chunk, i, box_at(chunk, i)) for i, _ in frames]
        assigned = identities.assign("cam1", "person", frames, dets)
        assert sum(len(v) for v in assigned.values()) == len(frames)
        seen.append(ids_of(assigned))
    assert all(ids == seen[0] for ids in seen)
    assert len(seen[0]) == 1


def test_a_walking_person_keeps_one_id_at_two_keyframes_per_second() -> None:
    """1.4 m/s, 0.5 m wide (100 px/m): 70 px between keyframes, more than the box is wide."""
    identities = KeyframeIdentities()
    ids: set[str] = set()
    for index in range(4):
        chunk = make_chunk(index=index)
        frames = keyframes(chunk)
        dets = []
        for i, _ in frames:
            x = 140.0 * (frame_ts(chunk, i) - EPOCH).total_seconds()
            dets.append(det(chunk, i, (x, 200.0, x + 50.0, 370.0)))
        assigned = identities.assign("cam1", "person", frames, dets)
        assert sum(len(v) for v in assigned.values()) == len(frames)
        ids |= set(assigned)
    assert len(ids) == 1


@pytest.mark.parametrize("rate", [2.0, 4.0])
def test_two_people_walking_past_each_other_keep_their_ids(rate: float) -> None:
    identities = KeyframeIdentities()
    left, right = walker(0.0, 80.0, y=100.0), walker(600.0, -80.0, y=100.0)
    trajectories: dict[str, list[float]] = {}
    for index in range(3):
        chunk = make_chunk(index=index)
        frames = keyframes(chunk, rate)
        dets = []
        for i, _ in frames:
            a, b = left(chunk, i), right(chunk, i)
            dets += [det(chunk, i, b, object_id=0), det(chunk, i, a, object_id=1)]  # shuffled ids
        for track_id, found in identities.assign("cam1", "person", frames, dets).items():
            trajectories.setdefault(track_id, []).extend(d.mask.box_xyxy[0] for d in found)
    assert len(trajectories) == 2
    for xs in trajectories.values():
        steps = np.diff(xs)
        assert (steps > 0).all() or (steps < 0).all()  # each id keeps one walker's direction


def test_a_lost_track_is_dropped_and_a_late_detection_starts_a_new_one() -> None:
    identities = KeyframeIdentities(new_suffix=suffixes())
    box = (100.0, 100.0, 140.0, 180.0)
    chunk = make_chunk(index=0)
    first = identities.assign("cam1", "person", keyframes(chunk), [det(chunk, 0, box)])
    gap_chunks = int(LOST_TRACK_S // 3) + 1
    for index in range(1, gap_chunks + 1):  # nothing seen for longer than LOST_TRACK_S
        empty = make_chunk(index=index)
        assert identities.assign("cam1", "person", keyframes(empty), []) == {}
    later = make_chunk(index=gap_chunks + 1)
    second = identities.assign("cam1", "person", keyframes(later), [det(later, 0, box)])
    assert ids_of(first) != ids_of(second)


def test_a_short_miss_keeps_the_id() -> None:
    identities = KeyframeIdentities()
    box = (100.0, 100.0, 140.0, 180.0)
    chunk = make_chunk()
    frames = keyframes(chunk)
    dets = [det(chunk, i, box) for i, _ in frames if i != 10]  # one keyframe missed
    assigned = identities.assign("cam1", "person", frames, dets)
    assert len(assigned) == 1


def test_low_confidence_detections_extend_tracks_but_never_start_them() -> None:
    identities = KeyframeIdentities()
    chunk = make_chunk()
    frames = keyframes(chunk)
    box = (100.0, 100.0, 140.0, 180.0)
    faint = [det(chunk, 0, (500, 0, 540, 80), conf=0.3)]
    assert identities.assign("cam1", "person", frames[:1], faint) == {}
    dets = [det(chunk, 5, box), det(chunk, 10, box, conf=0.3)]
    assigned = identities.assign("cam1", "person", frames[1:3], dets)
    assert [len(v) for v in assigned.values()] == [2]


def test_cameras_and_concepts_have_separate_streams() -> None:
    identities = KeyframeIdentities()
    box = (100.0, 100.0, 140.0, 180.0)
    one, two = make_chunk("cam1"), make_chunk("cam2")
    a = identities.assign("cam1", "person", keyframes(one)[:1], [det(one, 0, box)])
    b = identities.assign("cam2", "person", keyframes(two)[:1], [det(two, 0, box)])
    c = identities.assign("cam1", "forklift", keyframes(one)[:1], [det(one, 0, box)])
    assert len(ids_of(a) | ids_of(b) | ids_of(c)) == 3
    assert next(iter(b)).startswith("cam2:person:")
    assert next(iter(c)).startswith("cam1:forklift:")


def test_time_going_backwards_restarts_the_stream() -> None:
    identities = KeyframeIdentities()
    box = (100.0, 100.0, 140.0, 180.0)
    later, earlier = make_chunk(index=2), make_chunk(index=1)
    a = identities.assign("cam1", "person", keyframes(later)[:1], [det(later, 0, box)])
    b = identities.assign("cam1", "person", keyframes(earlier)[:1], [det(earlier, 0, box)])
    assert ids_of(a) != ids_of(b)


def test_new_bytetrack_counts_its_lost_buffer_in_reference_frames() -> None:
    tracker = new_bytetrack(1.5)
    assert tracker.maximum_time_without_update == pytest.approx(1.5)


# --- KeyframeTracker -------------------------------------------------------------------------


class ScriptedKeyframes:
    """A `KeyframeSegmenter` that draws each concept's objects from a function of the frame."""

    def __init__(
        self,
        world: World,
        *,
        billed: bool = True,
        backend: Backend = Backend.HOSTED,
    ) -> None:
        self.world = world
        self.billed = billed
        self.backend = backend
        self.calls: list[tuple[str, tuple[int, ...], tuple[str, ...]]] = []

    def segment_keyframes(
        self, chunk: Chunk, frames: Sequence[Keyframe], concepts: Sequence[str]
    ) -> KeyframeBatch:
        self.calls.append((chunk.chunk_id, tuple(f.frame_idx for f in frames), tuple(concepts)))
        found: dict[str, tuple[Detection, ...]] = {}
        for concept in concepts:
            dets = []
            for frame in frames:
                for object_id, box_at in enumerate(self.world.get(concept, ())):
                    box = box_at(chunk, frame.frame_idx)
                    dets.append(
                        det(chunk, frame.frame_idx, box, object_id=object_id).model_copy(
                            update={"concept": concept}
                        )
                    )
            found[concept] = tuple(dets)
        images = len(frames) * len(concepts)
        return KeyframeBatch(
            detections=found,
            requests=images if self.billed else 1,
            billed_images=images if self.billed else 0,
            backend=self.backend,
        )


def fake_decoder(calls: list[tuple[str, tuple[int, ...]]]) -> FrameDecoder:
    def decode(chunk: Chunk, indices: Sequence[int]) -> list[Keyframe]:
        calls.append((chunk.chunk_id, tuple(indices)))
        return [Keyframe(i, frame_ts(chunk, i), b"jpeg") for i in indices]

    return decode


def static_box(chunk: Chunk, frame_idx: int) -> Box:
    return (10.0, 10.0, 60.0, 60.0)


def make_tracker(
    world: World, **kwargs: object
) -> tuple[KeyframeTracker, ScriptedKeyframes, list[tuple[str, tuple[int, ...]]]]:
    segmenter = ScriptedKeyframes(world, **kwargs)  # type: ignore[arg-type]
    decoded: list[tuple[str, tuple[int, ...]]] = []
    tracker = KeyframeTracker(segmenter, decoder=fake_decoder(decoded))
    return tracker, segmenter, decoded


def test_segment_decodes_once_and_asks_for_every_dynamic_concept_together() -> None:
    world = {"person": [walker(0.0, 50.0)], "forklift": [walker(500.0, -50.0)]}
    tracker, segmenter, decoded = make_tracker(world)
    chunk = make_chunk()
    segmented = tracker.segment(chunk, ["person", "forklift", "person"])
    assert decoded == [(chunk.chunk_id, (0, 5, 10, 15, 20, 25))]
    assert segmenter.calls == [(chunk.chunk_id, (0, 5, 10, 15, 20, 25), ("person", "forklift"))]
    assert segmented.concepts == ("person", "forklift")
    assert [d.frame_idx for d in segmented.detections["forklift"]] == [0, 5, 10, 15, 20, 25]


def test_static_concepts_come_from_the_cache_spread_over_the_keyframes() -> None:
    world: World = {"person": [walker(0.0, 50.0)], "sink": [static_box]}
    tracker, segmenter, decoded = make_tracker(world)
    tracker.set_static(["sink"], generation="plan-1")
    for index in range(2):
        segmented = tracker.segment(make_chunk(index=index), ["person", "sink"])
        assert [d.frame_idx for d in segmented.detections["sink"]] == [0, 5, 10, 15, 20, 25]
    sink_calls = [c for c in segmenter.calls if c[2] == ("sink",)]
    assert sink_calls == [("cam1_000000", (0,), ("sink",))]  # one keyframe, once per plan
    assert ("cam1_000000", (0,)) in decoded
    tracker.set_static(["sink"], generation="plan-2")
    tracker.segment(make_chunk(index=2), ["sink"])
    assert len([c for c in segmenter.calls if c[2] == ("sink",)]) == 2
    assert tracker.cache.handle_coverage_changed({"camera_id": "cam1", "state": "moved"})


def test_static_only_chunks_decode_no_frame_after_the_cache_is_warm() -> None:
    tracker, segmenter, decoded = make_tracker({"sink": [static_box]})
    tracker.set_static(["sink"])
    tracker.segment(make_chunk(index=0), ["sink"])
    tracker.segment(make_chunk(index=1), ["sink"])
    assert len(decoded) == 1
    assert len(segmenter.calls) == 1
    reprompt = tracker.cache._segmenter.segment(make_chunk(), "sink", box_prompts={1: (0, 0, 1, 1)})
    assert reprompt == []  # a box re-prompt of a static concept makes no SAM call


def test_stitch_keeps_track_ids_across_chunks_stores_and_publishes() -> None:
    world = {"person": [walker(0.0, 50.0)], "forklift": [walker(800.0, -100.0)]}
    store = InMemoryTrackStore()
    bus = RecordingBus()
    segmenter = ScriptedKeyframes(world)
    tracker = KeyframeTracker(
        segmenter,
        store=store,
        bus=bus,
        decoder=fake_decoder([]),
        rates=KeyframeRates(default_fps=2.0),
    )
    ids: list[list[str]] = []
    for index in range(3):
        chunk = make_chunk(index=index)
        tracks = tracker.stitch(chunk, tracker.segment(chunk, ["person", "forklift"]))
        assert [t.concept for t in tracks] == ["person", "forklift"]
        for track in tracks:
            assert all(
                chunk.start_ts <= p.ts < chunk.start_ts + timedelta(seconds=3) for p in track.points
            )
            assert [p.frame_idx for p in track.points] == [0, 5, 10, 15, 20, 25]
        ids.append([t.track_id for t in tracks])
    assert ids[0] == ids[1] == ids[2]
    assert ids[0][0].startswith("cam1:person:")
    window = store.window("cam1", EPOCH, EPOCH + timedelta(seconds=9))
    assert {t.track_id for t in window} == set(ids[0])
    assert bus.topics().count(Topic.TRACKS_UPDATED) == 3
    point = window[0].points[0]
    assert point.foot_px == ((point.box_xyxy[0] + point.box_xyxy[2]) / 2, point.box_xyxy[3])


def test_usage_bills_hosted_images_in_video_frame_units() -> None:
    tracker, _, _ = make_tracker({"person": [walker(0.0, 50.0)]})
    chunk = make_chunk()
    assert tracker.estimate(chunk, 2) == round(2 * 6 * IMAGE_FRAME_EQUIVALENT)  # 150
    tracker.segment(chunk, ["person", "forklift"])
    assert tracker.take_chunk("cam1", chunk.chunk_id) == (12, 150)
    assert tracker.take_chunk("cam1", chunk.chunk_id) == (0, 0)
    assert tracker.totals("cam1") == (12, 150)
    assert tracker.totals("cam2") == (0, 0)


def test_usage_of_self_hosted_keyframes_is_free() -> None:
    tracker, _, _ = make_tracker({"person": [walker(0.0, 50.0)]}, billed=False)
    chunk = make_chunk()
    assert tracker.estimate(chunk, 3) == 0
    tracker.segment(chunk, ["person", "forklift"])
    assert tracker.take_chunk("cam1", chunk.chunk_id) == (1, 0)


def test_per_camera_rates_and_timings() -> None:
    segmenter = ScriptedKeyframes({"person": [walker(0.0, 50.0)]})
    ticks = itertools.count()
    timings = StageTimings(monotonic=lambda: float(next(ticks)))
    tracker = KeyframeTracker(
        segmenter,
        rates=KeyframeRates.parse(2.0, "fast=4"),
        decoder=fake_decoder([]),
        timings=timings,
    )
    assert len(tracker.indices(make_chunk("fast"))) == 15
    assert len(tracker.indices(make_chunk("cam1"))) == 6
    tracker.segment(make_chunk("fast"), ["person"])
    report = timings.report()
    assert report["decode"].n == 1
    assert report["sam"].n == 1


# --- speeds on keyframe tracks ---------------------------------------------------------------


@pytest.mark.parametrize(("speed_m_s", "fires"), [(1.5, True), (0.0, False)])
def test_moving_works_on_two_hertz_keyframe_tracks(speed_m_s: float, fires: bool) -> None:
    """`moving` reads speeds from the interpolated keyframes; jitter of a still box is not speed."""
    rng = np.random.default_rng(7)
    segmenter = ScriptedKeyframes({})
    tracker = KeyframeTracker(segmenter, decoder=fake_decoder([]))
    tracks = []
    for index in range(3):
        chunk = make_chunk(index=index)
        frames = keyframe_indices(chunk.n_frames, chunk.fps, 2.0)
        dets = []
        for i in frames:
            t = (frame_ts(chunk, i) - EPOCH).total_seconds()
            x = 100.0 + 100.0 * speed_m_s * t + rng.normal(0, 2.0)  # CAL: 100 px per metre
            dets.append(
                det(chunk, i, (x - 40, 300.0, x + 40, 400.0)).model_copy(
                    update={"concept": "forklift"}
                )
            )
        from rulecam.perception.tracking import SegmentedChunk  # noqa: PLC0415

        segmented = SegmentedChunk(chunk.chunk_id, ("forklift",), {"forklift": tuple(dets)})
        tracks += tracker.stitch(chunk, segmented)
    assert len({t.track_id for t in tracks}) == 1
    rule = make_rule(
        {"op": "moving", "object": "forklift", "speed_mps_gt": 0.5},
        concepts={"forklift": "forklift"},
    )
    found = evaluate(
        rule, tracks, calibration=CAL, zones={}, t0=EPOCH, t1=EPOCH + timedelta(seconds=9)
    )
    assert bool(found) is fires


# --- tracking metrics through the eval harness (#102, py-motmetrics) -----------------------


def test_keyframe_tracks_score_perfectly_on_the_eval_harness() -> None:
    """Three people (two crossing) over 4 chunks at 2 keyframes/s: IDF1 1.0, no ID switch."""
    from rulecam.eval.record import PredictedBox  # noqa: PLC0415
    from rulecam.eval.tracking import FrameBoxes, score_tracking  # noqa: PLC0415

    people = {
        "a": walker(0.0, 80.0, y=100.0),
        "b": walker(600.0, -80.0, y=100.0),
        "c": walker(200.0, 30.0, y=400.0),
    }
    identities = KeyframeIdentities()
    gt: dict[int, FrameBoxes] = {}
    predicted: list[PredictedBox] = []
    for index in range(4):
        chunk = make_chunk(index=index)
        frames = keyframes(chunk)
        dets = []
        for i, ts in frames:
            frame = round((ts - EPOCH).total_seconds() * chunk.fps) + 1
            boxes = gt.setdefault(frame, FrameBoxes())
            for n, (name, box_at) in enumerate(sorted(people.items(), reverse=index % 2 == 1)):
                boxes.ids.append(name)
                boxes.boxes.append(box_at(chunk, i))
                dets.append(det(chunk, i, box_at(chunk, i), object_id=n))
        for track_id, found in identities.assign("cam1", "person", frames, dets).items():
            predicted += [
                PredictedBox(
                    clip_s=(d.ts - EPOCH).total_seconds(),
                    track=track_id,
                    concept="person",
                    box_xyxy=d.mask.box_xyxy,
                )
                for d in found
            ]
    (scores,) = score_tracking({"person": gt}, predicted, fps=10.0)
    assert scores.gt_ids == 3
    assert scores.id_switches == 0
    assert scores.idf1 == pytest.approx(1.0)
    assert scores.mota == pytest.approx(1.0)
