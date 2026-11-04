"""The runtime strip renderer (P4-05): keyframes over the episode, overlays, privacy, failures."""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from rulecam.contracts import (
    CandidateEvent,
    Chunk,
    Keypoint,
    Measurement,
    PrivacyMode,
    Severity,
    Track,
    TrackPoint,
    Zone,
)
from rulecam.rules.backtest import DecisionContext
from rulecam.runtime import strips as strips_module
from rulecam.runtime.loop import ActivePlan
from rulecam.runtime.skeleton import GLYPH_COLOUR, GLYPH_LEGEND, PpeMark
from rulecam.runtime.strip_draw import (
    BAND_EDGE_COLOUR,
    BAND_HEADER,
    RULER_COLOUR,
    SUBJECT_COLOUR,
    TICK_LABEL,
    Frame,
    NoFaceDetector,
    Overlay,
    YuNetFaces,
    annotation_texts,
    band_height,
    legend,
)
from rulecam.runtime.strips import (
    EpisodeStrips,
    Keyframe,
    caption,
    decode_args,
    distance_ruler,
    face_detector,
    ffmpeg_decode,
    is_person,
    keyframe_overlay,
    nearest_point,
    pick_keyframes,
    ppe_marks,
    render_frames,
    rule_polygons,
    safe_name,
    strip_size,
    strip_span,
    unproject,
)
from tests.fakes import EPOCH, FakeClock, FakeVerifier, InMemoryTrackStore, make_chunk
from tests.rules.helpers import CAL, make_rule
from tests.runtime.fakes import SteadyTracker, make_loop, sink_plan

BACKGROUND = (60, 60, 60)
SENTINEL = (7, 250, 7)  # the person's pixels: must never appear in an anonymous strip
FORKLIFT = (0, 90, 200)
PERSON_BOX = (200.0, 200.0, 280.0, 360.0)  # chunk pixels (1280x720); strip is 640x360
FORKLIFT_BOX = (400.0, 200.0, 600.0, 360.0)
RULE = make_rule(
    {"op": "distance_m", "a": "person", "b": "forklift", "lt": 3.0},
    concepts={"person": "person", "forklift": "forklift"},
    rule_id="forklift-proximity",
)


def at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def track(track_id: str, concept: str, box: tuple[float, float, float, float]) -> Track:
    points = tuple(
        TrackPoint(
            ts=at(i / 10),
            frame_idx=i,
            box_xyxy=box,
            foot_px=((box[0] + box[2]) / 2, box[3]),
            confidence=0.9,
        )
        for i in range(60)
    )
    return Track(track_id=track_id, camera_id="cam1", concept=concept, points=points)


def candidate(start_s: float = 1.0, end_s: float = 4.0, **extra: object) -> CandidateEvent:
    fields: dict[str, object] = {
        "event_id": "ev/../1",
        "rule_id": RULE.id,
        "camera_id": "cam1",
        "ts_start": at(start_s),
        "ts_end": at(end_s),
        "severity": Severity.HIGH,
        "measurement": Measurement(value=2.6, unit="m"),
        "predicate_trace": {"subject": "cam1:person:1"},
        "strip_paths": (),
        "track_window_hash": "t" * 64,
        "plan_version_hash": "p" * 64,
        "rule_ir_hash": "r" * 64,
    }
    fields.update(extra)
    return CandidateEvent.model_validate(fields)


def context(*, calibrated: bool = True) -> DecisionContext:
    return DecisionContext(rule=RULE, calibration=CAL if calibrated else None, t0=at(0), t1=at(6))


def scene_frame(
    size: tuple[int, int], box: tuple[float, float, float, float] = PERSON_BOX
) -> Frame:
    """A strip-sized frame: grey floor, a sentinel-coloured person, a forklift."""
    width, height = size
    frame = np.full((height, width, 3), BACKGROUND, dtype=np.uint8)
    fx0, fy0, fx1, fy1 = (int(v / 2) for v in FORKLIFT_BOX)
    frame[fy0:fy1, fx0:fx1] = FORKLIFT
    px0, py0, px1, py1 = (int(v / 2) for v in box)
    frame[py0:py1, px0:px1] = SENTINEL
    return frame


def read_image(path: str) -> Frame:
    image = cv2.imread(path)
    assert image is not None
    return np.asarray(image, dtype=np.uint8)


class FakeDecode:
    def __init__(self, fail: Sequence[str] = ()) -> None:
        self.calls: list[tuple[str, tuple[int, ...]]] = []
        self.fail = set(fail)

    def __call__(self, chunk: Chunk, indices: Sequence[int], size: tuple[int, int]) -> list[Frame]:
        self.calls.append((chunk.chunk_id, tuple(indices)))
        if chunk.chunk_id in self.fail:
            raise subprocess.CalledProcessError(1, "ffmpeg")
        return [scene_frame(size) for _ in indices]


class Chunks:
    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = list(chunks)

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        return [c for c in self._chunks if c.camera_id == camera_id]


class RecordingLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kw: object) -> None:
        self.events.append((event, kw))

    warning = info

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


class Faces:
    """A fake face detector: always reports one fixed box."""

    def __init__(self, box: tuple[float, float, float, float]) -> None:
        self.box = box
        self.calls = 0

    def detect(self, frame: Frame) -> list[tuple[float, float, float, float]]:
        self.calls += 1
        return [self.box]


def store_with_scene() -> InMemoryTrackStore:
    store = InMemoryTrackStore()
    store.append(
        [
            track("cam1:person:1", "person", PERSON_BOX),
            track("cam1:forklift:1", "forklift", FORKLIFT_BOX),
        ]
    )
    return store


def renderer(
    tmp_path: Path, *, decode: FakeDecode | None = None, store: InMemoryTrackStore | None = None
) -> EpisodeStrips:
    return EpisodeStrips(
        chunks=Chunks([make_chunk(index=0), make_chunk(index=1)]),
        store=store if store is not None else store_with_scene(),
        out_dir=tmp_path / "strips",
        faces=NoFaceDetector(),
        decode=decode or FakeDecode(),
    )


# --- keyframe selection ----------------------------------------------------------------------


def test_keyframes_span_the_episode_across_chunks() -> None:
    chunks = [make_chunk(index=1), make_chunk(index=0), make_chunk(index=0)]  # any order, dupes
    keys = pick_keyframes(at(1.0), at(4.0), chunks, 6)
    assert len(keys) == 6
    assert (keys[0].ts, keys[-1].ts) == (at(1.0), at(4.0))
    assert [k.chunk.chunk_id for k in keys] == ["cam1_000000"] * 4 + ["cam1_000001"] * 2
    assert [k.frame_idx for k in keys] == [10, 16, 22, 28, 4, 10]
    assert all(a.ts < b.ts for a, b in itertools.pairwise(keys))


def test_keyframes_skip_gaps_and_merge_same_frames() -> None:
    assert [k.frame_idx for k in pick_keyframes(at(1.0), at(1.0), [make_chunk()], 6)] == [10]
    gap = pick_keyframes(at(1.0), at(8.0), [make_chunk(index=0)], 6)  # nothing after 3 s
    assert [round((k.ts - EPOCH).total_seconds(), 1) for k in gap] == [1.0, 2.4]
    end = pick_keyframes(at(3.0), at(3.0), [make_chunk(index=0)], 1)
    assert end[0].frame_idx == 29  # the last frame, never one past the end
    with pytest.raises(ValueError, match="at least one frame"):
        pick_keyframes(at(0), at(1), [make_chunk()], 0)


def test_short_episode_is_widened_backwards() -> None:
    assert strip_span(candidate(3.0, 3.5)) == (at(1.5), at(3.5))
    assert strip_span(candidate(1.0, 4.0)) == (at(1.0), at(4.0))


def test_strip_size_scales_down_to_640_and_stays_even() -> None:
    assert strip_size(make_chunk()) == (640, 360)
    small = make_chunk().model_copy(update={"width": 65, "height": 49})
    assert strip_size(small) == (64, 48)


def test_decode_args_pipe_raw_frames_never_to_a_file() -> None:
    args = decode_args(Path("c.mp4"), (3, 9), (640, 360))
    assert args[:2] == ["ffmpeg", "-nostdin"]
    assert "select='eq(n\\,3)+eq(n\\,9)',scale=640:360" in args
    assert args[-1] == "pipe:1"
    assert {"rawvideo", "bgr24"} <= set(args)
    assert ("-vsync" in args) or ("-fps_mode" in args)


# --- overlays --------------------------------------------------------------------------------


def test_overlay_highlights_subject_and_measures_distance() -> None:
    key = Keyframe(chunk=make_chunk(), frame_idx=20, ts=at(2.0))
    tracks = [
        track("cam1:person:1", "person", PERSON_BOX),
        track("cam1:forklift:1", "forklift", FORKLIFT_BOX),
    ]
    tracks.append(track("cam1:mop:1", "mop", (0, 0, 10, 10)))  # not a rule concept: not drawn
    overlay = keyframe_overlay(candidate(), context(), tracks, key, scale=0.5, position=(2, 6))
    assert [(i.concept, i.subject, i.person) for i in overlay.items] == [
        ("person", True, True),
        ("forklift", False, False),
    ]
    assert overlay.items[0].box == (100.0, 100.0, 140.0, 180.0)
    assert overlay.ruler is not None
    # To the forklift's footprint (its box's bottom edge), the geometry `distance_m` measures,
    # not to the middle of the 2 m-wide vehicle (2.6 m).
    assert overlay.ruler.metres == pytest.approx(1.6)
    assert overlay.ruler.a == (120.0, 180.0)
    assert overlay.ruler.b == pytest.approx((200.0, 180.0))
    assert overlay.ruler.tick == ((120.0, 180.0), (170.0, 180.0))  # 1 m = 100 chunk px
    assert overlay.caption == "2/6  t+1.0s  forklift-proximity  measured 2.60 m"
    uncalibrated = keyframe_overlay(
        candidate(), context(calibrated=False), tracks, key, scale=0.5, position=(1, 1)
    )
    assert uncalibrated.ruler is None
    elsewhere = keyframe_overlay(
        candidate(),
        context().model_copy(update={"calibration": CAL.model_copy(update={"camera_id": "cam9"})}),
        tracks,
        key,
        scale=0.5,
        position=(1, 1),
    )
    assert elsewhere.ruler is None


def test_overlay_without_a_visible_subject_has_no_ruler() -> None:
    key = Keyframe(chunk=make_chunk(), frame_idx=20, ts=at(2.0))
    tracks = [track("cam1:forklift:1", "forklift", FORKLIFT_BOX)]
    overlay = keyframe_overlay(candidate(), context(), tracks, key, scale=0.5, position=(1, 1))
    assert overlay.ruler is None
    late = Keyframe(chunk=make_chunk(), frame_idx=0, ts=at(30.0))  # tracks ended long ago
    assert (
        keyframe_overlay(candidate(), context(), tracks, late, scale=0.5, position=(1, 1)).items
        == ()
    )


def test_overlays_are_drawn_on_the_frame() -> None:
    key = Keyframe(chunk=make_chunk(), frame_idx=20, ts=at(2.0))
    tracks = [
        track("cam1:person:1", "person", PERSON_BOX),
        track("cam1:forklift:1", "forklift", FORKLIFT_BOX),
    ]
    overlay = keyframe_overlay(candidate(), context(), tracks, key, scale=0.5, position=(1, 6))
    raw = scene_frame((640, 360))
    (out,) = render_frames([raw], [overlay], PrivacyMode.STANDARD, NoFaceDetector())
    assert tuple(out[140, 100]) == SUBJECT_COLOUR  # left edge of the subject's box
    # the ruler's line up to the forklift's footprint (its box's bottom-left corner)
    assert np.abs(out[180, 185].astype(int) - np.asarray(RULER_COLOUR)).sum() < 90
    assert np.abs(out[180, 193].astype(int) - np.asarray(RULER_COLOUR)).sum() < 90
    band = band_height(overlay, 640, strict=False)
    assert out.shape == (360 + band, 640, 3)  # the band is appended, never drawn over the scene
    assert np.array_equal(out[:20], raw[:20])  # no caption bar on the scene any more
    assert (out[360:362] == BAND_EDGE_COLOUR).all()
    assert out[362:].mean() < 40  # black band with white text


def _relabelled(overlay: Overlay, *, concepts: bool) -> Overlay:
    """The same shapes with other words: caption, zone names and (optionally) concept names."""
    items = (
        tuple(replace(i, concept=f"{i.concept}-renamed") for i in overlay.items)
        if concepts
        else overlay.items
    )
    zones = tuple((f"{name}-renamed", poly) for name, poly in overlay.zones)
    caption = "9/9  t+9.9s  some-other-rule  measured 9.99 m"
    return replace(overlay, items=items, zones=zones, caption=caption)


def test_no_text_is_drawn_on_the_scene() -> None:
    """Live finding: Glimmer copied our captions and labels into `scene_text`."""
    key = Keyframe(chunk=make_chunk(), frame_idx=20, ts=at(2.0))
    tracks = [
        track("cam1:person:1", "person", PERSON_BOX),
        track("cam1:forklift:1", "forklift", FORKLIFT_BOX),
    ]
    overlay = replace(
        keyframe_overlay(candidate(), context(), tracks, key, scale=0.5, position=(1, 6)),
        zones=(("dock", ((10.0, 300.0), (60.0, 300.0), (60.0, 340.0))),),
    )
    # Concept names pick box colours in standard mode, so they are renamed in strict mode only.
    for mode, concepts in ((PrivacyMode.STANDARD, False), (PrivacyMode.STRICT, True)):
        raw = scene_frame((640, 360))
        other = _relabelled(overlay, concepts=concepts)
        (one,) = render_frames([raw], [overlay], mode, NoFaceDetector())
        (two,) = render_frames([raw], [other], mode, NoFaceDetector())
        assert np.array_equal(one[:360], two[:360])  # the words change nothing on the scene
        assert not np.array_equal(one[360:], two[360:])  # they are all in the band


def test_the_band_lists_exactly_what_it_shows() -> None:
    key = Keyframe(chunk=make_chunk(), frame_idx=20, ts=at(2.0))
    tracks = [
        track("cam1:person:1", "person", PERSON_BOX),
        track("cam1:forklift:1", "forklift", FORKLIFT_BOX),
    ]
    overlay = keyframe_overlay(candidate(), context(), tracks, key, scale=0.5, position=(2, 6))
    assert [text for text, _ in legend(overlay, strict=False)] == [
        "subject: person",
        "forklift",
        "distance 1.6 m",
        TICK_LABEL,
    ]
    assert annotation_texts(overlay) == (
        BAND_HEADER,
        "2/6  t+1.0s  forklift-proximity  measured 2.60 m",
        "2/6",
        "t+1.0s",
        "forklift-proximity",
        "measured 2.60 m",
        "subject: person",
        "forklift",
        "distance 1.6 m",
        TICK_LABEL,
        "1.6 m",
    )
    bare = Overlay(items=())
    assert annotation_texts(bare) == (BAND_HEADER,)
    assert band_height(bare, 640, strict=False) < band_height(overlay, 640, strict=False)
    # A narrow frame wraps the legend onto more rows.
    assert band_height(overlay, 120, strict=False) > band_height(overlay, 640, strict=False)


def test_rule_polygons_are_scaled_and_filtered_by_camera() -> None:
    rule = make_rule({"op": "inside", "object": "person", "zone": "dock"})
    zone = Zone(
        zone_id="dock", camera_id="cam1", kind="polygon", polygon_px=((0, 0), (100, 0), (100, 50))
    )
    assert rule_polygons(rule, {"dock": zone}, "cam1", 0.5) == (
        ("dock", ((0, 0), (50, 0), (50, 25))),
    )
    assert rule_polygons(rule, {"dock": zone}, "cam2", 0.5) == ()
    assert rule_polygons(rule, {}, "cam1", 0.5) == ()  # unknown zone: nothing to draw


def test_caption_without_measurement() -> None:
    key = Keyframe(chunk=make_chunk(), frame_idx=0, ts=at(1.0))
    assert caption(candidate(measurement=None), key, 1, 4) == "1/4  t+0.0s  forklift-proximity"


# --- privacy ---------------------------------------------------------------------------------


def _scene_overlay(subject_box: tuple[float, float, float, float] = PERSON_BOX) -> Overlay:
    key = Keyframe(chunk=make_chunk(), frame_idx=20, ts=at(2.0))
    tracks = [
        track("cam1:person:1", "person", subject_box),
        track("cam1:forklift:1", "forklift", FORKLIFT_BOX),
    ]
    return keyframe_overlay(
        candidate(), context(calibrated=False), tracks, key, scale=0.5, position=(1, 1)
    )


@pytest.mark.parametrize("mode", [PrivacyMode.ANONYMOUS, PrivacyMode.STRICT])
def test_no_person_pixel_survives_anonymous_and_strict(mode: PrivacyMode) -> None:
    # The person walks across, so the plate sees the floor behind them in other frames.
    boxes = [(200.0 + 60 * i, 200.0, 280.0 + 60 * i, 360.0) for i in range(4)]
    raws = [scene_frame((640, 360), box) for box in boxes]
    overlays = [_scene_overlay(box) for box in boxes]
    outs = render_frames(raws, overlays, mode, NoFaceDetector())
    for out in outs:
        assert not np.all(out == SENTINEL, axis=2).any()
        near = np.abs(out.astype(int) - np.asarray(SENTINEL)).sum(axis=2) < 60
        assert not near.any()
    # The forklift (an object, not a person) is still visible to the verifier.
    first = outs[0]
    if mode is PrivacyMode.ANONYMOUS:
        assert tuple(first[150, 280]) == FORKLIFT
    else:
        assert len(set(first[150, 280].tolist())) == 1  # greyscale: no colour at all


def test_strict_mode_is_greyscale_except_the_subject_box() -> None:
    raws = [scene_frame((640, 360))]
    (out,) = render_frames(raws, [_scene_overlay()], PrivacyMode.STRICT, NoFaceDetector())
    scene = out[:360]  # the band's legend swatches are coloured on purpose
    colourful = (scene.max(axis=2).astype(int) - scene.min(axis=2)) > 10
    ys, xs = np.nonzero(colourful)
    assert colourful.any()  # the red subject box
    assert xs.min() >= 90
    assert xs.max() <= 260
    assert ys.min() >= 20
    assert ys.max() <= 190


def test_never_seen_empty_pixels_are_neutral_grey() -> None:
    raws = [scene_frame((640, 360))] * 3  # the person never moves
    (out,) = render_frames(raws[:1], [_scene_overlay()], PrivacyMode.ANONYMOUS, NoFaceDetector())
    assert tuple(out[130, 112]) == (128, 128, 128)  # inside the person, off the stick figure


def test_standard_mode_blurs_detected_faces_and_tracked_heads() -> None:
    rng = np.random.default_rng(0)
    raw = rng.integers(0, 256, size=(360, 640, 3), dtype=np.uint8)
    faces = Faces((400.0, 50.0, 440.0, 90.0))  # an untracked face somewhere else
    overlay = _scene_overlay()
    (out,) = render_frames([raw], [overlay], PrivacyMode.STANDARD, faces)
    assert faces.calls == 1
    assert out[55:85, 405:435].std() < raw[55:85, 405:435].std() / 3  # the detected face
    assert out[103:117, 103:137].std() < raw[103:117, 103:137].std() / 3  # the person's head
    assert np.array_equal(out[250:300, 400:600], raw[250:300, 400:600])  # the rest is untouched


def test_anonymous_mode_also_blurs_faces_the_detector_finds() -> None:
    rng = np.random.default_rng(1)
    raw = rng.integers(0, 256, size=(360, 640, 3), dtype=np.uint8)
    faces = Faces((210.0, 110.0, 250.0, 150.0))  # inside the forklift's box: a missed person
    (out,) = render_frames([raw], [_scene_overlay()], PrivacyMode.ANONYMOUS, faces)
    assert out[118:142, 218:242].std() < raw[118:142, 218:242].std() / 3


def test_pose_keypoints_are_drawn_as_a_skeleton() -> None:
    kps = tuple(Keypoint(x=212.0, y=220.0 + 8 * k, score=0.9) for k in range(17))
    tr = track("cam1:person:1", "person", PERSON_BOX)
    tr = tr.model_copy(
        update={"points": tuple(p.model_copy(update={"keypoints": kps}) for p in tr.points)}
    )
    key = Keyframe(chunk=make_chunk(), frame_idx=20, ts=at(2.0))
    overlay = keyframe_overlay(
        candidate(), context(calibrated=False), [tr], key, scale=0.5, position=(1, 1)
    )
    assert overlay.items[0].keypoints is not None
    (out,) = render_frames(
        [scene_frame((640, 360))], [overlay], PrivacyMode.ANONYMOUS, NoFaceDetector()
    )
    assert not np.all(out == SENTINEL, axis=2).any()
    column = out[145:170, 106]  # the bones run down x = 106, where no stick figure would be
    assert (np.abs(column.astype(int) - 128).sum(axis=1) > 30).any()


# --- PPE marks (anonymous and strict) --------------------------------------------------------

PPE_RULE = make_rule(
    {
        "op": "any",
        "of": [
            {"op": "without", "subject": "person", "item": "hard_hat", "region": "top_25pct"},
            {"op": "without", "subject": "person", "item": "vest", "region": "full"},
            {"op": "without", "subject": "person", "item": "glove", "region": "hands"},
        ],
    },
    concepts={"person": "person", "hard_hat": "hard hat", "vest": "safety vest", "glove": "glove"},
    rule_id="ppe",
)
HAT_BOX = (225.0, 195.0, 255.0, 225.0)  # centre (240, 210): the person's top quarter


def _ppe_tracks(*extra: Track) -> list[Track]:
    return [track("cam1:person:1", "person", PERSON_BOX), *extra]


def _present(tracks: Sequence[Track]) -> list[tuple[Track, TrackPoint]]:
    return [(t, t.points[20]) for t in tracks]


def test_ppe_marks_follow_the_rules_without_regions() -> None:
    hat = track("cam1:hard_hat:1", "hard_hat", HAT_BOX)
    low_vest = track("cam1:vest:1", "vest", (600.0, 600.0, 610.0, 610.0))  # outside the person
    present = _present(_ppe_tracks(hat, low_vest))
    person, point = present[0]
    assert ppe_marks(PPE_RULE, person, point, present) == (
        PpeMark(region="full", seen=False),
        PpeMark(region="hands", seen=False),
        PpeMark(region="top_25pct", seen=True),
    )
    # A hat lower down the body is not on the head; without pose, hands are the lower half.
    low_hat = track("cam1:hard_hat:1", "hard_hat", (225.0, 330.0, 255.0, 350.0))
    glove = track("cam1:glove:1", "glove", (225.0, 330.0, 255.0, 350.0))
    present = _present(_ppe_tracks(low_hat, glove))
    marks = {m.region: m.seen for m in ppe_marks(PPE_RULE, *present[0], present)}
    assert marks == {"full": False, "hands": True, "top_25pct": False}
    assert ppe_marks(RULE, *present[0], present) == ()  # no `without` predicate: no marks


def test_hand_marks_use_the_wrists_when_pose_is_known() -> None:
    wrists = {9: (210.0, 300.0), 10: (270.0, 300.0)}
    kps = tuple(
        Keypoint(x=wrists.get(k, (0.0, 0.0))[0], y=wrists.get(k, (0.0, 0.0))[1], score=0.9)
        for k in range(17)
    )
    person = track("cam1:person:1", "person", PERSON_BOX)
    point = person.points[20].model_copy(update={"keypoints": kps})
    near = track("cam1:glove:1", "glove", (205.0, 295.0, 215.0, 305.0))  # on the left wrist
    far = track("cam1:glove:1", "glove", (235.0, 340.0, 245.0, 350.0))  # lower half, no wrist
    for glove, seen in ((near, True), (far, False)):
        present = [(person, point), (glove, glove.points[20])]
        assert PpeMark(region="hands", seen=seen) in ppe_marks(PPE_RULE, person, point, present)


def test_two_items_on_one_region_give_one_mark_seen_only_if_both_are() -> None:
    rule = make_rule(
        {
            "op": "any",
            "of": [
                {"op": "without", "subject": "person", "item": "hard_hat", "region": "top_25pct"},
                {"op": "without", "subject": "person", "item": "hood", "region": "top_25pct"},
            ],
        },
        concepts={"person": "person", "hard_hat": "hard hat", "hood": "hood"},
    )
    present = _present(_ppe_tracks(track("cam1:hard_hat:1", "hard_hat", HAT_BOX)))
    assert ppe_marks(rule, *present[0], present) == (PpeMark(region="top_25pct", seen=False),)


def test_only_anonymous_and_strict_overlays_carry_ppe_marks() -> None:
    key = Keyframe(chunk=make_chunk(), frame_idx=20, ts=at(2.0))
    tracks = _ppe_tracks(track("cam1:hard_hat:1", "hard_hat", HAT_BOX))
    ppe_context = DecisionContext(rule=PPE_RULE, calibration=None, t0=at(0), t1=at(6))
    plain = keyframe_overlay(candidate(), ppe_context, tracks, key, scale=0.5, position=(1, 1))
    marked = keyframe_overlay(
        candidate(), ppe_context, tracks, key, scale=0.5, position=(1, 1), ppe=True
    )
    assert all(item.ppe == () for item in plain.items)
    assert GLYPH_LEGEND not in annotation_texts(plain)
    person, hat = marked.items
    assert PpeMark(region="top_25pct", seen=True) in person.ppe
    assert hat.ppe == ()  # objects carry no marks
    assert GLYPH_LEGEND in annotation_texts(marked)
    (out,) = render_frames(
        [scene_frame((640, 360))], [marked], PrivacyMode.STRICT, NoFaceDetector()
    )
    white = np.all(out == GLYPH_COLOUR, axis=2)
    vest = white[122:142, 117:123]  # the stick figure's torso (not seen: crossed, not filled)
    assert vest.any()
    assert not vest.all()
    assert white[103:105, 116:124].all()  # the cap over the head, below the subject box
    assert not (np.abs(out.astype(int) - np.asarray(SENTINEL)).sum(axis=2) < 60).any()


def test_anonymous_strip_lists_the_ppe_legend_but_standard_does_not(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    store.append(_ppe_tracks(track("cam1:hard_hat:1", "hard_hat", HAT_BOX)))
    ppe_context = DecisionContext(rule=PPE_RULE, calibration=None, t0=at(0), t1=at(6))
    folder = tmp_path / "strips" / "cam1" / "ev_.._1"
    for mode, listed in ((PrivacyMode.ANONYMOUS, True), (PrivacyMode.STANDARD, False)):
        out = renderer(tmp_path, store=store).render(
            candidate(rule_id="ppe"), make_chunk(index=1), mode, ppe_context
        )
        assert out.strip_paths
        drawn = json.loads((folder / "annotations.json").read_text("utf-8"))["drawn"]
        assert (GLYPH_LEGEND in drawn) is listed


# --- the renderer, end to end with fakes -----------------------------------------------------


def test_render_writes_the_strip_and_returns_its_paths(tmp_path: Path) -> None:
    decode = FakeDecode()
    out = renderer(tmp_path, decode=decode).render(
        candidate(), make_chunk(index=1), PrivacyMode.STANDARD, context()
    )
    assert len(out.strip_paths) == 6
    folder = tmp_path / "strips" / "cam1" / "ev_.._1"
    assert out.strip_paths == tuple(str(folder / f"{k}.jpg") for k in range(1, 7))
    assert all(Path(p).read_bytes().startswith(b"\xff\xd8\xff") for p in out.strip_paths)
    assert [c for c, _ in decode.calls] == ["cam1_000000", "cam1_000001"]  # one call per chunk
    assert not list((tmp_path / "strips" / "cam1").glob("*.part"))
    image = read_image(out.strip_paths[0])
    assert image.shape[1:] == (640, 3)
    assert image.shape[0] > 360  # the frame plus Rulecam's annotation band below it
    drawn = json.loads((folder / "annotations.json").read_text("utf-8"))["drawn"]
    assert BAND_HEADER in drawn
    assert any(text.startswith("1/6") and "forklift-proximity" in text for text in drawn)


def test_rerender_replaces_the_old_strip(tmp_path: Path) -> None:
    strips = renderer(tmp_path)
    first = strips.render(candidate(), make_chunk(index=1), PrivacyMode.STANDARD, context())
    short = strips.render(candidate(3.9, 4.0), make_chunk(index=1), PrivacyMode.STANDARD, context())
    assert first.strip_paths[0] == short.strip_paths[0]
    assert all(Path(p).is_file() for p in short.strip_paths)


def test_anonymous_strip_files_hold_no_person_pixels(tmp_path: Path) -> None:
    out = renderer(tmp_path).render(
        candidate(), make_chunk(index=1), PrivacyMode.ANONYMOUS, context()
    )
    assert out.strip_paths
    for path in out.strip_paths:
        image = read_image(path).astype(int)
        near = np.abs(image - np.asarray(SENTINEL)).sum(axis=2) < 60
        assert not near.any()


def test_a_failing_chunk_is_skipped_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = RecordingLog()
    monkeypatch.setattr(strips_module, "_log", log)
    out = renderer(tmp_path, decode=FakeDecode(fail=["cam1_000000"])).render(
        candidate(), make_chunk(index=1), PrivacyMode.STANDARD, context()
    )
    assert len(out.strip_paths) == 2  # the frames of the chunk that decoded
    assert log.names() == ["strip_chunk_failed", "strip_rendered"]


def test_no_frames_gives_an_empty_strip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = RecordingLog()
    monkeypatch.setattr(strips_module, "_log", log)
    decode = FakeDecode(fail=["cam1_000000", "cam1_000001"])
    out = renderer(tmp_path, decode=decode).render(
        candidate(), make_chunk(index=1), PrivacyMode.STANDARD, context()
    )
    assert out.strip_paths == ()
    assert log.names() == ["strip_chunk_failed", "strip_chunk_failed", "strip_no_frames"]


def test_any_other_failure_gives_an_empty_strip_and_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenStore(InMemoryTrackStore):
        def window(self, camera_id: str, t0: datetime, t1: datetime) -> list[Track]:
            raise RuntimeError("store down")

    log = RecordingLog()
    monkeypatch.setattr(strips_module, "_log", log)
    out = renderer(tmp_path, store=BrokenStore()).render(
        candidate(strip_paths=("stale.jpg",)), make_chunk(index=1), PrivacyMode.STANDARD, context()
    )
    assert out.strip_paths == ()
    ((name, fields),) = log.events
    assert name == "strip_render_failed"
    assert fields["error"] == "RuntimeError: store down"


def test_unwritable_strip_leaves_no_partial_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cv2, "imwrite", lambda *a, **k: False)
    out = renderer(tmp_path).render(
        candidate(), make_chunk(index=1), PrivacyMode.STANDARD, context()
    )
    assert out.strip_paths == ()
    assert not list((tmp_path / "strips").rglob("*.jpg"))
    assert not list((tmp_path / "strips").rglob("*.part"))


def test_loop_hands_the_strip_to_glimmer_and_survives_a_broken_one(tmp_path: Path) -> None:
    clock = FakeClock()
    clock.advance(3.0)
    store = InMemoryTrackStore()
    verifier = FakeVerifier()
    strips = EpisodeStrips(
        chunks=Chunks([]),
        store=store,
        out_dir=tmp_path / "strips",
        faces=NoFaceDetector(),
        decode=FakeDecode(),
    )
    loop, _, _, _ = make_loop(
        tmp_path, clock, verifier, strips=strips, tracker=SteadyTracker(store), store=store
    )
    loop.set_plan(_sink_plan())
    loop.handle_chunk(make_chunk(index=0))
    ((seen, _),) = verifier.calls
    assert len(seen.strip_paths) >= 4
    assert all(Path(p).is_file() for p in seen.strip_paths)

    broken_verifier = FakeVerifier()
    store2 = InMemoryTrackStore()
    broken = EpisodeStrips(
        chunks=Chunks([]),
        store=store2,
        out_dir=tmp_path / "strips2",
        faces=NoFaceDetector(),
        decode=FakeDecode(fail=["cam1_000000"]),
    )
    loop2, _, _, _ = make_loop(
        tmp_path / "b",
        clock,
        broken_verifier,
        strips=broken,
        tracker=SteadyTracker(store2),
        store=store2,
    )
    loop2.set_plan(_sink_plan())
    loop2.handle_chunk(make_chunk(index=0))
    ((seen2, _),) = broken_verifier.calls
    assert seen2.strip_paths == ()  # verification still ran, on the trace alone


def _sink_plan() -> ActivePlan:
    return sink_plan(privacy_mode=PrivacyMode.STANDARD)


# --- helpers ---------------------------------------------------------------------------------


def test_is_person_reads_the_concept_and_its_prompt() -> None:
    rule = make_rule(
        {"op": "count", "object": "crew", "gt": 0}, concepts={"crew": "kitchen worker"}
    )
    assert is_person("crew", rule)
    assert is_person("forklift_driver", RULE)
    assert is_person("hands", RULE)
    assert not is_person("forklift", RULE)


def test_safe_name_never_escapes_the_folder() -> None:
    assert safe_name("../../etc/passwd") == "_.._etc_passwd"
    assert safe_name("...") == "_"
    assert safe_name("cam1") == "cam1"
    assert len(safe_name("x" * 500)) == 120


def test_nearest_point_respects_the_tolerance() -> None:
    tr = track("t", "person", PERSON_BOX)
    point = nearest_point(tr, at(2.04))
    assert point is not None
    assert point.ts == at(2.0)
    assert nearest_point(tr, at(7.0)) is None
    assert nearest_point(tr.model_copy(update={"points": ()}), at(0)) is None


def test_unproject_inverts_the_calibration() -> None:
    assert unproject(CAL, (2.4, 3.6)) == pytest.approx((240.0, 360.0))
    flat = CAL.model_copy(
        update={"homography": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0))}
    )
    assert unproject(flat, (1.0, 1.0)) is None  # singular homography


def test_distance_ruler_picks_the_nearest_object() -> None:
    subject = track("s", "person", PERSON_BOX).points[0]
    near = track("n", "forklift", (300.0, 200.0, 340.0, 360.0)).points[0]
    far = track("f", "forklift", FORKLIFT_BOX).points[0]
    ruler = distance_ruler(subject, [far, near], CAL, 1.0)
    assert ruler is not None
    assert ruler.metres == pytest.approx(0.6)  # to the near forklift's footprint corner
    assert distance_ruler(subject, [], CAL, 1.0) is None
    same_spot = distance_ruler(subject, [subject], CAL, 1.0)
    assert same_spot is not None
    assert same_spot.metres == 0.0


def test_face_detector_falls_back_without_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = RecordingLog()
    monkeypatch.setattr(strips_module, "_log", log)
    assert isinstance(face_detector(None), NoFaceDetector)
    assert isinstance(face_detector(tmp_path / "missing.onnx"), NoFaceDetector)
    junk = tmp_path / "junk.onnx"
    junk.write_bytes(b"not a model")
    assert isinstance(face_detector(junk), NoFaceDetector)
    assert log.names() == ["face_detector_missing"] * 3


def test_yunet_wrapper_maps_detections_to_boxes(monkeypatch: pytest.MonkeyPatch) -> None:
    class Net:
        def __init__(self, faces: object) -> None:
            self.faces = faces
            self.size: tuple[int, int] | None = None

        def setInputSize(self, size: tuple[int, int]) -> None:  # noqa: N802 - OpenCV's name
            self.size = size

        def detect(self, frame: Frame) -> tuple[int, object]:
            return 1, self.faces

    found = np.asarray([[10, 20, 30, 40, *([0.0] * 10), 0.9]], dtype=np.float32)
    for faces, expected in [(found, [(10.0, 20.0, 40.0, 60.0)]), (None, [])]:
        net = Net(faces)
        monkeypatch.setattr(cv2.FaceDetectorYN, "create", lambda *a, net=net: net)
        detector = YuNetFaces(Path("model.onnx"))
        assert detector.detect(np.zeros((48, 64, 3), dtype=np.uint8)) == expected
        assert net.size == (64, 48)


# --- real ffmpeg -----------------------------------------------------------------------------


def _clip(path: Path, colour: str) -> Path:
    subprocess.run(  # noqa: S603 - test fixture
        [  # noqa: S607
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=64x48:rate=10:duration=3,drawbox=x=0:y=40:w=8:h=8:color={colour}:t=fill",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_ffmpeg_decodes_the_chosen_frames_in_memory(tmp_path: Path) -> None:
    clip = _clip(tmp_path / "a.mp4", "red")
    chunk = make_chunk().model_copy(update={"path": str(clip), "width": 64, "height": 48})
    frames = ffmpeg_decode(chunk, (2, 12, 25), (64, 48))
    assert [f.shape for f in frames] == [(48, 64, 3)] * 3
    assert len({f.tobytes() for f in frames}) == 3  # three different moments
    with pytest.raises(ValueError, match="frames of"):
        ffmpeg_decode(chunk, (2, 500), (64, 48))  # frame 500 does not exist
    with pytest.raises(FileNotFoundError):
        ffmpeg_decode(chunk.model_copy(update={"path": str(tmp_path / "gone.mp4")}), (1,), (64, 48))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_real_chunks_on_disk_give_a_strip_end_to_end(tmp_path: Path) -> None:
    def chunk(index: int, colour: str) -> Chunk:
        clip = _clip(tmp_path / f"{index}.mp4", colour)
        return make_chunk(index=index).model_copy(
            update={"path": str(clip), "width": 64, "height": 48}
        )

    first, second = chunk(0, "red"), chunk(1, "blue")
    store = InMemoryTrackStore()
    store.append([track("cam1:person:1", "person", (20.0, 10.0, 40.0, 46.0))])
    strips = EpisodeStrips(
        chunks=Chunks([first]), store=store, out_dir=tmp_path / "strips", faces=NoFaceDetector()
    )
    out = strips.render(candidate(), second, PrivacyMode.STANDARD, context(calibrated=False))
    assert len(out.strip_paths) == 6
    corners = [read_image(p)[44, 2] for p in out.strip_paths]  # red chunk first, then blue
    assert corners[0][2] > 150
    assert corners[0][0] < 100
    assert corners[-1][0] > 150
    assert corners[-1][2] < 100


# --- pose on the strip (add-on 3.4: RTMPose-l body7) ------------------------------------------


class FakePose:
    """A pose model that puts every keypoint at the box's centre (strip pixels)."""

    def __init__(self, fail: bool = False) -> None:
        self.boxes: list[tuple[float, float, float, float]] = []
        self.fail = fail

    def estimate(
        self, frame: Frame, box_xyxy: tuple[float, float, float, float]
    ) -> tuple[Keypoint, ...]:
        self.boxes.append(box_xyxy)
        if self.fail:
            raise RuntimeError("onnx session failed")
        x0, y0, x1, y1 = box_xyxy
        return tuple(
            Keypoint(x=(x0 + x1) / 2, y=y0 + (y1 - y0) * k / 16, score=0.9) for k in range(17)
        )


def test_pose_pass_estimates_people_in_strip_pixels_and_returns_chunk_pixels() -> None:
    pose = FakePose()
    key = Keyframe(chunk=make_chunk(), frame_idx=20, ts=at(2.0))
    tracks = [
        track("cam1:person:1", "person", PERSON_BOX),
        track("cam1:forklift:1", "forklift", FORKLIFT_BOX),
    ]
    frame = scene_frame((640, 360))
    overlay = keyframe_overlay(
        candidate(),
        context(),
        tracks,
        key,
        scale=0.5,
        position=(1, 1),
        pose=strips_module.PosePass(pose, frame, 0.5),
    )
    assert pose.boxes == [(100.0, 100.0, 140.0, 180.0)]  # only the person, in strip pixels
    person = next(item for item in overlay.items if item.person)
    forklift = next(item for item in overlay.items if not item.person)
    assert forklift.keypoints is None
    assert person.keypoints is not None
    assert person.keypoints[0][:2] == pytest.approx((120.0, 100.0))  # back in strip pixels


def test_pose_pass_keeps_known_keypoints_and_survives_a_failing_model() -> None:
    point = track("cam1:person:1", "person", PERSON_BOX).points[0]
    known = point.model_copy(update={"keypoints": (Keypoint(x=1.0, y=2.0, score=0.5),) * 17})
    frame = scene_frame((640, 360))
    pose = FakePose()
    assert strips_module.PosePass(pose, frame, 0.5)(known) is known
    assert pose.boxes == []
    assert strips_module.PosePass(FakePose(fail=True), frame, 0.5)(point) is point


def test_strip_with_a_pose_model_draws_the_real_skeleton(tmp_path: Path) -> None:
    pose = FakePose()
    strips = EpisodeStrips(
        chunks=Chunks([make_chunk(index=0), make_chunk(index=1)]),
        store=store_with_scene(),
        out_dir=tmp_path / "strips",
        faces=NoFaceDetector(),
        decode=FakeDecode(),
        raw=True,
        pose=pose,
    )
    out = strips.render(candidate(), make_chunk(index=1), PrivacyMode.STANDARD, context())
    assert len(out.strip_paths) == 6
    assert len(pose.boxes) == 6  # the person, once per keyframe
    image = read_image(out.strip_paths[0])
    column = image[110:170, 120].astype(int)  # the skeleton runs down the box's centre line
    assert (np.abs(column - np.array(SENTINEL)).sum(axis=1) > 60).any()


def test_episode_frames_decode_the_strip_keyframes_at_full_size() -> None:
    decode = FakeDecode(fail=["cam1_000000"])
    sizes: list[tuple[int, int]] = []

    def sized(chunk: Chunk, indices: Sequence[int], size: tuple[int, int]) -> list[Frame]:
        sizes.append(size)
        return decode(chunk, indices, size)

    source = strips_module.EpisodeFrames(
        Chunks([make_chunk(index=0), make_chunk(index=1), make_chunk("other", index=1)]), sized
    )
    frames = source.frames(candidate(), 3)
    assert sizes == [(1280, 720), (1280, 720)]  # the chunk's own size, one call per chunk
    assert [ts for ts, _ in frames] == sorted(ts for ts, _ in frames)
    assert frames
    assert all(frame.shape == (720, 1280, 3) for _, frame in frames)
    assert strips_module.EpisodeFrames(Chunks([]), sized).frames(candidate(), 3) == []
