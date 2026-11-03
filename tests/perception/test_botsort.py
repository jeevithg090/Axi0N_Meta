"""BoT-SORT with person re-ID between keyframes (add-on 3.1).

The stream logic runs everywhere with a fake engine. The tests marked `boxmot` drive BoxMOT's real
`BotSort` (no weights: the vectors are supplied) and are skipped without the `reid` extra.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from rulecam.contracts import Detection
from rulecam.perception.botsort import (
    REID_DIM,
    BotSortStream,
    buffered_iou,
    by_concept,
    new_botsort_stream,
)
from rulecam.perception.keyframe_tracker import (
    ByteTrackStream,
    KeyframeIdentities,
    StreamTracker,
)
from rulecam.perception.keyframes import Embedding, Embeddings
from rulecam.rules.operators import is_person_like
from tests.fakes import EPOCH, make_chunk
from tests.perception.test_keyframe_tracker import det, keyframes, walker


class FakeEngine:
    """Records what BoT-SORT would get; every detection becomes track `100 + row`."""

    def __init__(self) -> None:
        self.calls: list[tuple[np.ndarray, np.ndarray, np.ndarray, float]] = []

    def update(
        self, boxes: np.ndarray, scores: np.ndarray, embeddings: np.ndarray, timestamp_s: float
    ) -> tuple[np.ndarray, np.ndarray]:
        self.calls.append((boxes, scores, embeddings, timestamp_s))
        rows = np.arange(len(boxes))
        return rows + 100, rows

    def alive(self) -> set[int]:
        return {100}


def unit(index: int, width: int = REID_DIM) -> Embedding:
    vector = np.zeros(width)
    vector[index] = 1.0
    return tuple(float(v) for v in vector)


def test_buffered_iou_overlaps_boxes_that_plain_iou_keeps_apart() -> None:
    a = np.array([[0.0, 0.0, 10.0, 10.0]])
    b = np.array([[12.0, 0.0, 22.0, 10.0], [100.0, 100.0, 110.0, 110.0]])
    found = buffered_iou(a, b, 0.5)
    assert found.shape == (1, 2)
    assert found[0, 0] == pytest.approx(8 * 20 / (2 * 400 - 8 * 20))
    assert found[0, 1] == 0.0
    assert buffered_iou(a, a, 0.0)[0, 0] == 1.0
    assert buffered_iou(np.zeros((0, 4)), b).shape == (0, 2)


def test_stream_sends_boxes_scores_and_unit_vectors() -> None:
    engine = FakeEngine()
    stream = BotSortStream(engine, width=4)
    chunk = make_chunk()
    dets = [det(chunk, 0, (0, 0, 10, 20), conf=0.9), det(chunk, 0, (5, 5, 9, 9), conf=0.3)]
    pairs = stream.step(dets, [(0.0, 3.0, 0.0, 4.0), None], 12.5)
    assert pairs == [(0, 100), (1, 101)]
    boxes, scores, vectors, ts = engine.calls[0]
    assert boxes.tolist() == [[0, 0, 10, 20], [5, 5, 9, 9]]
    assert scores.tolist() == pytest.approx([0.9, 0.3])
    assert vectors[0].tolist() == pytest.approx([0.0, 0.6, 0.0, 0.8])
    assert np.linalg.norm(vectors[1]) == pytest.approx(1.0)  # a random placeholder
    assert ts == 12.5
    assert stream.alive() == {100}


def test_missing_or_misshapen_vectors_get_reproducible_random_placeholders() -> None:
    first = BotSortStream(FakeEngine(), width=8).vectors([None, (1.0, 0.0)])
    again = BotSortStream(FakeEngine(), width=8).vectors([None, (1.0, 0.0)])
    assert first.shape == (2, 8)
    assert np.allclose(first, again)
    assert abs(float(first[0] @ first[1])) < 0.99  # two placeholders are not alike
    assert BotSortStream(FakeEngine()).vectors([]).shape == (0, REID_DIM)


def test_empty_keyframes_still_advance_the_engine() -> None:
    engine = FakeEngine()
    assert BotSortStream(engine).step([], [], 3.0) == []
    assert engine.calls[0][0].shape == (0, 4)


def test_by_concept_puts_people_on_the_person_tracker() -> None:
    built: list[tuple[str, float]] = []

    def person(lost_track_s: float) -> StreamTracker:
        built.append(("person", lost_track_s))
        return BotSortStream(FakeEngine())

    factory = by_concept(is_person_like, person)
    assert isinstance(factory("worker", 2.0), BotSortStream)
    assert isinstance(factory("forklift", 2.0), ByteTrackStream)
    assert built == [("person", 2.0)]
    assert isinstance(by_concept(is_person_like, None)("person", 2.0), ByteTrackStream)


def test_identities_pass_each_detections_vector_to_the_stream() -> None:
    engine = FakeEngine()
    identities = KeyframeIdentities(new_tracker=lambda c, s: BotSortStream(engine, width=4))
    chunk = make_chunk()
    frames = keyframes(chunk)[:1]
    dets = [det(chunk, 0, (0, 0, 10, 20)), det(chunk, 0, (50, 0, 60, 20), object_id=1)]
    vectors: Embeddings = {(0, 1): (1.0, 0.0, 0.0, 0.0)}
    assigned = identities.assign("cam1", "person", frames, dets, vectors)
    assert len(assigned) == 2
    sent = engine.calls[0][2]
    assert sent[1].tolist() == [1.0, 0.0, 0.0, 0.0]


# --- BoxMOT's real BotSort (the `reid` extra) -------------------------------------------------


def botsort_identities() -> KeyframeIdentities:
    pytest.importorskip("boxmot")
    return KeyframeIdentities(new_tracker=by_concept(is_person_like, new_botsort_stream))


def run(
    identities: KeyframeIdentities,
    people: Sequence[tuple[float, float, Embedding | None]],
    chunks: int = 3,
    rate: float = 2.0,
) -> dict[str, list[float]]:
    """Walk `(x0, speed px/s, vector)` people through `chunks` chunks; ids -> left edges."""
    trajectories: dict[str, list[float]] = {}
    for index in range(chunks):
        chunk = make_chunk(index=index)
        frames = keyframes(chunk, rate)
        dets: list[Detection] = []
        vectors: dict[tuple[int, int], Embedding] = {}
        for i, _ in frames:
            for object_id, (x0, speed, vector) in enumerate(people):
                dets.append(det(chunk, i, walker(x0, speed)(chunk, i), object_id=object_id))
                if vector is not None:
                    vectors[(i, object_id)] = vector
        for track_id, found in identities.assign("cam1", "person", frames, dets, vectors).items():
            trajectories.setdefault(track_id, []).extend(d.mask.box_xyxy[0] for d in found)
    return trajectories


def test_boxmot_names_a_person_on_the_first_keyframe_and_keeps_a_walker() -> None:
    identities = botsort_identities()
    chunk = make_chunk()
    first = identities.assign(
        "cam1", "person", keyframes(chunk)[:1], [det(chunk, 0, (0, 0, 40, 80))]
    )
    assert len(first) == 1
    walking = run(botsort_identities(), [(0.0, 120.0, unit(0))], chunks=4)  # 1.5 widths a step
    assert len(walking) == 1


@pytest.mark.parametrize("rate", [2.0, 4.0])
def test_boxmot_keeps_two_crossing_people_apart(rate: float) -> None:
    trajectories = run(botsort_identities(), [(0.0, 80.0, unit(0)), (600.0, -80.0, unit(1))])
    assert len(trajectories) == 2
    for xs in trajectories.values():
        steps = np.diff(xs)
        assert (steps > 0).all() or (steps < 0).all()


def test_boxmot_without_vectors_still_tracks_by_place() -> None:
    assert len(run(botsort_identities(), [(0.0, 60.0, None)], chunks=3)) == 1


def test_boxmot_low_confidence_extends_but_never_starts_and_lost_tracks_drop() -> None:
    identities = botsort_identities()
    chunk = make_chunk()
    frames = keyframes(chunk)
    box = (100.0, 100.0, 140.0, 180.0)
    faint = [det(chunk, 0, (500, 0, 540, 80), conf=0.3)]
    assert identities.assign("cam1", "person", frames[:1], faint) == {}
    dets = [det(chunk, 5, box), det(chunk, 10, box, conf=0.3)]
    first = identities.assign("cam1", "person", frames[1:3], dets)
    assert [len(v) for v in first.values()] == [2]
    for index in (1, 2):  # 6 s unseen: longer than LOST_TRACK_S
        empty = make_chunk(index=index)
        assert identities.assign("cam1", "person", keyframes(empty), []) == {}
    later = make_chunk(index=3)
    second = identities.assign("cam1", "person", keyframes(later)[:1], [det(later, 0, box)])
    assert set(first) != set(second)
    assert (later.start_ts - EPOCH).total_seconds() > 0
