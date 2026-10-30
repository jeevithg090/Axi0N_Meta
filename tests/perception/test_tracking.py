"""Cross-chunk tracking and identity hand-off, against `FakeSegmenter` (no network)."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pytest

from rulecam.bus import Bus, Topic
from rulecam.clients.meta_hosted.sam_masks import decode_wire_mask
from rulecam.clients.meta_hosted.sam_wire import mask_rle, output_text, parse_output
from rulecam.contracts import Chunk, Detection, Mask
from rulecam.contracts.core import BoxXYXY
from rulecam.perception.masks import NO_MASK, encode_rle, foot_point, mask_foot
from rulecam.perception.tracking import (
    Tracker,
    TracksUpdated,
    box_iou,
    group_by_object,
    match_by_iou,
    prompt_batches,
)
from tests.clients.meta_hosted.transport_fakes import SAM_FIXTURES
from tests.fakes import FakeSegmenter, InMemoryTrackStore, make_chunk

PERSON: BoxXYXY = (100.0, 100.0, 140.0, 200.0)


def shifted(box: BoxXYXY, dx: float, dy: float = 0.0) -> BoxXYXY:
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def det(chunk: Chunk, frame: int, object_id: int, box: BoxXYXY, rle: str = NO_MASK) -> Detection:
    return Detection(
        camera_id=chunk.camera_id,
        frame_idx=frame,
        ts=chunk.start_ts + timedelta(seconds=frame / chunk.fps),
        concept="person",
        object_id=object_id,
        mask=Mask(rle=rle, box_xyxy=box, confidence=0.9),
    )


def whole_chunk(chunk: Chunk, object_id: int, box: BoxXYXY) -> list[Detection]:
    return [det(chunk, f, object_id, box) for f in range(chunk.n_frames)]


def sequential_ids() -> Callable[[], str]:
    counter = itertools.count()
    return lambda: f"{next(counter):08x}"


class Scripted:
    """A FakeSegmenter whose answers depend on the chunk and on the last call's box prompts."""

    def __init__(
        self,
        text: Callable[[Chunk], list[Detection]],
        boxes: Callable[[Chunk, dict[int, BoxXYXY]], list[Detection]] | None = None,
    ) -> None:
        self.fake = FakeSegmenter(responder=self._respond)
        self._text = text
        self._boxes = boxes

    def _respond(self, chunk: Chunk, concept: str) -> list[Detection]:
        prompts = self.fake.calls[-1].box_prompts
        if prompts:
            return self._boxes(chunk, prompts) if self._boxes else []
        return self._text(chunk)


def box_echo(dx: float) -> Callable[[Chunk, dict[int, BoxXYXY]], list[Detection]]:
    """SAM continuing each prompted box, moved by `dx` pixels, under the prompt's id."""

    def respond(chunk: Chunk, prompts: dict[int, BoxXYXY]) -> list[Detection]:
        return [
            d for pid, box in prompts.items() for d in whole_chunk(chunk, pid, shifted(box, dx))
        ]

    return respond


def test_two_chunk_handoff_keeps_track_id_when_object_moves_5px() -> None:
    first, second = make_chunk(index=0), make_chunk(index=1)
    sam = Scripted(
        text=lambda c: whole_chunk(
            c, 0 if c is first else 7, PERSON if c is first else shifted(PERSON, 5)
        ),
        boxes=box_echo(5.0),
    )
    tracker = Tracker(sam.fake, new_suffix=sequential_ids())

    [before] = tracker.update(first, "person")
    after = tracker.update(second, "person")

    assert [t.track_id for t in after] == [before.track_id]  # no duplicate from the concept pass
    assert before.track_id == "cam1:person:00000000"
    assert after[0].points[0].box_xyxy == shifted(PERSON, 5)
    reprompt = sam.fake.calls[-1]
    assert reprompt.box_prompts == {1: PERSON}
    assert reprompt.direction == "forward"


def test_iou_fallback_matches_and_new_object_gets_new_id() -> None:
    first, second = make_chunk(index=0), make_chunk(index=1)
    far: BoxXYXY = (600.0, 300.0, 640.0, 400.0)

    def text(chunk: Chunk) -> list[Detection]:
        if chunk is first:
            return whole_chunk(chunk, 0, PERSON)
        return whole_chunk(chunk, 3, shifted(PERSON, 5)) + whole_chunk(chunk, 4, far)

    sam = Scripted(text=text)
    tracker = Tracker(sam.fake, reprompt=False, new_suffix=sequential_ids())

    [before] = tracker.update(first, "person")
    after = {t.points[0].box_xyxy: t.track_id for t in tracker.update(second, "person")}

    assert after[shifted(PERSON, 5)] == before.track_id
    assert after[far] not in {before.track_id}
    assert all(call.box_prompts is None for call in sam.fake.calls)


def test_fallback_also_covers_objects_the_reprompt_missed() -> None:
    first, second = make_chunk(index=0), make_chunk(index=1)
    sam = Scripted(
        text=lambda c: whole_chunk(
            c, 0 if c is first else 9, shifted(PERSON, 0 if c is first else 4)
        ),
        boxes=lambda chunk, prompts: [],  # SAM lost the box prompt
    )
    tracker = Tracker(sam.fake)

    [before] = tracker.update(first, "person")
    [after] = tracker.update(second, "person")

    assert after.track_id == before.track_id


def test_low_iou_is_not_a_match() -> None:
    first, second = make_chunk(index=0), make_chunk(index=1)
    sam = Scripted(text=lambda c: whole_chunk(c, 0, PERSON if c is first else shifted(PERSON, 35)))
    tracker = Tracker(sam.fake, reprompt=False)

    [before] = tracker.update(first, "person")
    [after] = tracker.update(second, "person")

    assert box_iou(PERSON, shifted(PERSON, 35)) < 0.3
    assert after.track_id != before.track_id


def test_twenty_objects_are_reprompted_in_batches_of_16_and_4() -> None:
    first, second = make_chunk(index=0), make_chunk(index=1)
    boxes = [shifted(PERSON, 50.0 * i) for i in range(20)]
    sam = Scripted(
        text=lambda c: (
            [d for i, b in enumerate(boxes) for d in whole_chunk(c, i, b)] if c is first else []
        ),
        boxes=box_echo(2.0),
    )
    tracker = Tracker(sam.fake)

    before = tracker.update(first, "person")
    after = tracker.update(second, "person")

    sizes = [len(call.box_prompts) for call in sam.fake.calls if call.box_prompts]
    assert sizes == [16, 4]
    assert {t.track_id for t in after} == {t.track_id for t in before}
    assert len(after) == 20


def test_track_ids_are_opaque_and_unique() -> None:
    chunk = make_chunk()
    sam = Scripted(
        text=lambda c: whole_chunk(c, 0, PERSON) + whole_chunk(c, 1, shifted(PERSON, 300))
    )

    ids = [t.track_id for t in Tracker(sam.fake).update(chunk, "person")]

    assert len(set(ids)) == 2
    for track_id in ids:
        camera, concept, suffix = track_id.split(":")
        assert (camera, concept) == ("cam1", "person")
        assert len(suffix) == 8
        int(suffix, 16)


def test_no_handoff_across_a_gap_or_from_tracks_that_left_early() -> None:
    first = make_chunk(index=0)
    late = make_chunk(index=0, start=first.start_ts + timedelta(seconds=10))
    sam = Scripted(text=lambda c: whole_chunk(c, 0, PERSON), boxes=box_echo(0))
    tracker = Tracker(sam.fake)

    [before] = tracker.update(first, "person")
    [after] = tracker.update(late, "person")

    assert after.track_id != before.track_id
    assert all(call.box_prompts is None for call in sam.fake.calls)

    left = Scripted(text=lambda c: [det(c, f, 0, PERSON) for f in range(10)], boxes=box_echo(0))
    early_exit = Tracker(left.fake)
    early_exit.update(make_chunk(index=0), "person")
    early_exit.update(make_chunk(index=1), "person")
    assert all(call.box_prompts is None for call in left.fake.calls)


def test_foot_point_from_lowest_mask_pixels_with_box_fallback() -> None:
    chunk = make_chunk()
    raster = np.zeros((chunk.height, chunk.width), np.uint8)
    raster[100:200, 100:140] = 1
    raster[190:200, 100:110] = 0  # the lowest rows lean right: foot x is right of centre
    sam = Scripted(
        text=lambda c: [det(c, 0, 0, PERSON, encode_rle(raster)), det(c, 1, 0, PERSON)],
    )

    [track] = Tracker(sam.fake).update(chunk, "person")

    with_mask, without = track.points
    assert with_mask.foot_px[1] == 199.0
    assert with_mask.foot_px[0] > 120.0
    assert without.foot_px == (120.0, 200.0)
    assert with_mask.foot_m is None


def test_foot_m_uses_the_injected_pixel_to_metre_mapping() -> None:
    sam = Scripted(text=lambda c: whole_chunk(c, 0, PERSON))

    [track] = Tracker(sam.fake, px_to_m=lambda p: (p[0] / 100, p[1] / 100)).update(
        make_chunk(), "person"
    )

    assert track.points[0].foot_m == pytest.approx((1.2, 2.0))


def test_process_chunk_stores_tracks_and_publishes_tracks_updated() -> None:
    chunk = make_chunk()
    bus = Bus()
    published: list[object] = []
    bus.publish = lambda topic, payload: published.append((topic, payload))  # type: ignore[method-assign]
    store = InMemoryTrackStore()
    sam = Scripted(text=lambda c: whole_chunk(c, 0, PERSON))

    tracks = Tracker(sam.fake, store=store, bus=bus).process_chunk(chunk, ["person", "person"])

    assert len(tracks) == 1
    assert len(sam.fake.calls) == 1
    end = chunk.start_ts + timedelta(seconds=3)
    assert store.window("cam1", chunk.start_ts, end) == tracks
    assert published == [
        (Topic.TRACKS_UPDATED, TracksUpdated(camera_id="cam1", t0=chunk.start_ts, t1=end))
    ]


def test_helpers() -> None:
    assert box_iou(PERSON, PERSON) == 1.0
    assert box_iou((0, 0, 0, 0), (0, 0, 0, 0)) == 0.0
    assert match_by_iou([], [PERSON]) == []
    assert match_by_iou(
        [PERSON, shifted(PERSON, 300)], [shifted(PERSON, 301), shifted(PERSON, 2)]
    ) == [
        (0, 1),
        (1, 0),
    ]
    assert [len(b) for b in prompt_batches([PERSON] * 33)] == [16, 16, 1]
    chunk = make_chunk()
    grouped = group_by_object(
        [det(chunk, 2, 1, PERSON), det(chunk, 0, 1, PERSON), det(chunk, 0, 1, (0, 0, 1, 1))]
    )
    assert [d.frame_idx for d in grouped[1]] == [0, 2]
    assert grouped[1][0].mask.box_xyxy == PERSON  # the first detection per frame wins
    assert mask_foot(np.zeros((4, 4), np.uint8)) is None


# --- a two-minute synthetic walk: fragment rate ---------------------------------------------------


@dataclass(frozen=True)
class Walker:
    x0: float
    y0: float
    vx: float  # px per frame
    vy: float

    def box(self, frame: int) -> BoxXYXY:
        x, y = self.x0 + self.vx * frame, self.y0 + self.vy * frame
        return (x, y, x + 40.0, y + 100.0)


WALKERS = (Walker(50, 100, 0.8, 0.1), Walker(1100, 300, -0.7, 0.05), Walker(400, 500, 0.2, -0.3))


def synthetic_world(seed: int) -> Callable[..., Scripted]:
    rng = np.random.default_rng(seed)

    def jitter(box: BoxXYXY) -> BoxXYXY:
        dx, dy = rng.normal(0, 1.5, 2)
        return shifted(box, float(dx), float(dy))

    def frame0(chunk: Chunk) -> int:
        return round((chunk.start_ts - make_chunk().start_ts).total_seconds() * chunk.fps)

    def text(chunk: Chunk) -> list[Detection]:
        ids = rng.permutation(len(WALKERS)) + 10  # SAM ids are not stable across chunks
        return [
            det(chunk, f, int(ids[w]), jitter(walker.box(frame0(chunk) + f)))
            for w, walker in enumerate(WALKERS)
            for f in range(chunk.n_frames)
        ]

    def boxes(chunk: Chunk, prompts: dict[int, BoxXYXY]) -> list[Detection]:
        out = []
        for pid, prompt in prompts.items():
            walker = max(WALKERS, key=lambda w: box_iou(w.box(frame0(chunk)), prompt))
            out += [
                det(chunk, f, pid, jitter(walker.box(frame0(chunk) + f)))
                for f in range(chunk.n_frames)
            ]
        return out

    return lambda **kwargs: Scripted(
        text=text, boxes=boxes if kwargs.get("reprompt", True) else None
    )


@pytest.mark.parametrize("reprompt", [True, False])
def test_two_minute_synthetic_walk_fragment_rate_is_under_ten_percent(reprompt: bool) -> None:
    sam = synthetic_world(seed=42)(reprompt=reprompt)
    tracker = Tracker(sam.fake, reprompt=reprompt)
    store = InMemoryTrackStore()

    for index in range(40):  # 40 chunks x 3 s = 2 minutes
        store.append(tracker.update(make_chunk(index=index), "person"))

    start = make_chunk().start_ts
    tracks = store.window("cam1", start, start + timedelta(minutes=2))
    ids = {t.track_id for t in tracks}
    fragment_rate = (len(ids) - len(WALKERS)) / len(ids)
    assert fragment_rate < 0.10
    assert len(ids) == len(WALKERS)


def test_foot_point_on_a_real_decoded_sam_mask() -> None:
    reply = json.loads((SAM_FIXTURES / "live" / "video_rectangle.json").read_text("utf-8"))
    record = parse_output(output_text(reply["json"]))[0]  # box 122..197 x 262..277

    foot = foot_point(mask_rle(record, decode_wire_mask), record.box_xyxy)

    assert foot[1] == 277.0  # the mask's lowest row is the box's bottom row
    assert 122.0 <= foot[0] <= 197.0
    assert abs(foot[0] - 159.5) < 3.0  # close to the box centre for a filled rectangle
