from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from contextlib import aclosing
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rulecam.bus import Bus, Message, Topic
from rulecam.contracts import CandidateEvent, Chunk, Severity
from rulecam.perception.coverage import (
    BoolMask,
    ChunkSignals,
    CoverageReason,
    CoverageReference,
    CoverageState,
    CoverageThresholds,
    CoverageWatchdog,
    Frame,
    Hysteresis,
    OutcomeStatus,
    RuleOutcome,
    advance,
    classify,
    classify_outcome,
    make_reference,
    measure_chunk,
    outcome_from_candidate,
    read_frames,
    resize_to_width,
    tally_compliance,
    to_gray,
)
from rulecam.perception.intervals import Interval
from tests.fakes import EPOCH, make_chunk

WIDTH, HEIGHT = 640, 360


# --- synthetic frames -------------------------------------------------------------------------


def scene(seed: int = 0) -> Frame:
    """A textured BGR scene: a coarse random field plus sharp-edged rectangles."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(30, 226, size=(HEIGHT // 16, WIDTH // 16), dtype=np.uint8)
    gray = cv2.resize(coarse, (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)
    for _ in range(120):
        x, y = int(rng.integers(0, WIDTH - 20)), int(rng.integers(0, HEIGHT - 20))
        h, w = int(rng.integers(8, 40)), int(rng.integers(8, 40))
        gray[y : y + h, x : x + w] = int(rng.integers(0, 256))
    return np.asarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), dtype=np.uint8)


def live(frame: Frame, seed: int, sigma: float = 3.0) -> Frame:
    """The same view with fresh sensor noise, like consecutive frames of a working camera."""
    rng = np.random.default_rng(seed)
    noisy = frame.astype(np.float32) + rng.normal(0.0, sigma, frame.shape)
    return np.clip(noisy, 0, 255).astype(np.uint8)


def chunk_of(view: Frame, n: int = 8, sigma: float = 3.0) -> list[Frame]:
    return [live(view, seed, sigma) for seed in range(n)]


def tray(view: Frame, fraction: float, level: int = 90) -> Frame:
    covered = view.copy()
    covered[:, : int(WIDTH * fraction)] = level
    return covered


def rect_mask(x0: int, y0: int, x1: int, y1: int) -> BoolMask:
    mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


BASE = scene()
GOOD = chunk_of(BASE)
BAD = chunk_of(tray(BASE, 0.7), sigma=2.0)
SINK = rect_mask(100, 100, 200, 200)


def verdict(
    frames: Sequence[Frame],
    reference: CoverageReference | None = None,
    anchors: Mapping[str, BoolMask | None] | None = None,
) -> CoverageReason | None:
    return classify(measure_chunk(frames, reference=reference, anchors=anchors))


# --- each condition ---------------------------------------------------------------------------


def test_working_camera_is_ok() -> None:
    signals = measure_chunk(GOOD)
    assert classify(signals) is None
    assert signals.max_diff is not None
    assert signals.max_diff > 1.0
    assert signals.uniform_fraction < 0.1


def test_identical_frames_are_frozen() -> None:
    signals = measure_chunk([BASE] * 8)
    assert signals.identical
    assert classify(signals) is CoverageReason.FROZEN


def test_near_zero_difference_is_frozen() -> None:
    frames = [BASE.copy() for _ in range(8)]
    for i, frame in enumerate(frames):
        frame[0, i] = 255 - frame[0, i]
    signals = measure_chunk(frames)
    assert not signals.identical
    assert classify(signals) is CoverageReason.FROZEN


def test_single_frame_cannot_be_frozen() -> None:
    signals = measure_chunk([BASE])
    assert signals.max_diff is None
    assert not signals.identical
    assert classify(signals) is None


def test_dark_below_luma_20() -> None:
    assert verdict(chunk_of((BASE * 0.1).astype(np.uint8), sigma=1.0)) is CoverageReason.DARK


def test_dim_but_textured_scene_is_not_dark() -> None:
    signals = measure_chunk(chunk_of((BASE * 0.25).astype(np.uint8)))
    assert signals.mean_luma > 20
    assert classify(signals) is None


def test_uniform_region_over_60_percent_is_blocked() -> None:
    signals = measure_chunk(BAD)
    assert signals.uniform_fraction > 0.6
    assert classify(signals) is CoverageReason.BLOCKED


def test_uniform_region_under_60_percent_is_ok() -> None:
    signals = measure_chunk(chunk_of(tray(BASE, 0.4), sigma=2.0))
    assert 0.3 < signals.uniform_fraction < 0.6
    assert classify(signals) is None


def test_missing_anchor_is_blocked() -> None:
    reference = make_reference(BASE, {"sink": SINK})
    assert verdict(GOOD, reference=reference, anchors={"sink": SINK}) is None
    assert verdict(GOOD, reference=reference, anchors={"sink": None}) is CoverageReason.BLOCKED
    assert verdict(GOOD, reference=reference, anchors={}) is CoverageReason.BLOCKED
    empty = np.zeros_like(SINK)
    assert verdict(GOOD, reference=reference, anchors={"sink": empty}) is CoverageReason.BLOCKED


def test_low_iou_anchor_is_blocked() -> None:
    reference = make_reference(BASE, {"sink": SINK})
    occluded = rect_mask(140, 140, 160, 160)  # same centroid, IoU 0.04
    signals = measure_chunk(GOOD, reference=reference, anchors={"sink": occluded})
    assert signals.anchors_low_iou == ("sink",)
    assert classify(signals) is CoverageReason.BLOCKED


def test_anchor_shift_over_5_percent_is_moved() -> None:
    reference = make_reference(BASE, {"sink": SINK, "door": rect_mask(400, 50, 450, 300)})
    shifted = {"sink": np.roll(SINK, 64, axis=1), "door": rect_mask(464, 50, 514, 300)}
    signals = measure_chunk(GOOD, reference=reference, anchors=shifted)
    assert signals.anchor_shift_frac == pytest.approx(0.1)
    assert classify(signals) is CoverageReason.MOVED


def test_small_anchor_shift_is_ok() -> None:
    reference = make_reference(BASE, {"sink": SINK})
    signals = measure_chunk(GOOD, reference=reference, anchors={"sink": np.roll(SINK, 6, axis=1)})
    assert signals.anchor_shift_frac == pytest.approx(6 / WIDTH)
    assert classify(signals) is None


def test_anchor_masks_at_another_resolution_are_rescaled() -> None:
    reference = make_reference(BASE, {"sink": SINK})
    half = cv2.resize(SINK.astype(np.uint8), (WIDTH // 2, HEIGHT // 2)).astype(bool)
    signals = measure_chunk(GOOD, reference=reference, anchors={"sink": half})
    assert signals.anchors_low_iou == ()
    assert classify(signals) is None


def test_orb_homography_detects_a_moved_camera() -> None:
    reference = make_reference(BASE)
    moved = measure_chunk(chunk_of(np.roll(BASE, 64, axis=1)), reference=reference)
    assert moved.orb_shift_frac == pytest.approx(0.1, abs=0.01)
    assert classify(moved) is CoverageReason.MOVED
    nudged = measure_chunk(chunk_of(np.roll(BASE, 6, axis=1)), reference=reference)
    assert classify(nudged) is None


def test_orb_without_texture_gives_no_shift() -> None:
    blank = np.full((HEIGHT, WIDTH), 128, dtype=np.uint8)
    signals = measure_chunk(chunk_of(BASE), reference=make_reference(blank))
    assert signals.orb_shift_frac is None


def test_reason_precedence() -> None:
    black = np.zeros_like(BASE)
    assert verdict([black] * 4) is CoverageReason.DARK  # also identical and uniform
    frozen_tray = tray(BASE, 0.7)
    assert verdict([frozen_tray] * 4) is CoverageReason.BLOCKED  # also identical
    reference = make_reference(BASE, {"sink": SINK})
    moved_view = chunk_of(np.roll(BASE, 64, axis=1))
    assert verdict(moved_view, reference=reference, anchors={"sink": None}) is (
        CoverageReason.MOVED
    )


def test_classify_uses_custom_thresholds() -> None:
    signals = ChunkSignals(n_frames=2, mean_luma=30.0, identical=False, uniform_fraction=0.0)
    assert classify(signals) is None
    assert classify(signals, CoverageThresholds(dark_luma=40.0)) is CoverageReason.DARK


def test_frame_validation() -> None:
    with pytest.raises(ValueError, match="at least one frame"):
        measure_chunk([])
    with pytest.raises(ValueError, match="uint8"):
        to_gray(BASE.astype(np.float32))
    with pytest.raises(ValueError, match="grayscale or BGR"):
        to_gray(np.zeros((4, 4, 4), dtype=np.uint8))
    small = np.zeros((10, 20), dtype=np.uint8)
    assert resize_to_width(small, 160) is small
    assert to_gray(small) is small


# --- hysteresis -------------------------------------------------------------------------------


def run(raws: Sequence[CoverageReason | None], start: Hysteresis | None = None) -> list[Hysteresis]:
    machine = start or Hysteresis()
    states = []
    for raw in raws:
        machine = advance(machine, raw)
        states.append(machine)
    return states


D, B, F = CoverageReason.DARK, CoverageReason.BLOCKED, CoverageReason.FROZEN


def test_one_bad_chunk_does_not_enter_blind() -> None:
    assert [m.state for m in run([D, None, D, None])] == [CoverageState.OK] * 4


def test_two_bad_chunks_enter_blind_with_the_latest_reason() -> None:
    states = run([D, B])
    assert [m.status for m in states] == [None, B]


def test_recovery_needs_two_good_chunks_in_a_row() -> None:
    states = run([None, D, None, None], start=Hysteresis(status=D))
    assert [m.status for m in states] == [D, D, D, None]


def test_reason_change_while_blind_needs_two_matching_chunks() -> None:
    assert [m.status for m in run([B, F, B, B], start=Hysteresis(status=D))] == [D, D, D, B]
    assert [m.status for m in run([None, B, B], start=Hysteresis(status=D))] == [D, D, B]


def test_custom_hysteresis_lengths() -> None:
    machine = advance(Hysteresis(), D, enter_chunks=1)
    assert machine.status is D
    machine = advance(machine, None, recover_chunks=3)
    machine = advance(machine, None, recover_chunks=3)
    assert machine.status is D
    assert advance(machine, None, recover_chunks=3).status is None


# --- the watchdog: bus, accounting ------------------------------------------------------------


async def _collect(bus: Bus, n: int) -> list[Message]:
    received: list[Message] = []
    async with aclosing(bus.subscribe(Topic.COVERAGE_CHANGED)) as messages:
        async for message in messages:
            received.append(message)
            if len(received) == n:
                break
    return received


def feed(watchdog: CoverageWatchdog, pattern: str, camera_id: str = "cam1") -> None:
    """Observe one 3 s chunk per character: `g` good, `b` blocked by a tray."""
    for index, kind in enumerate(pattern):
        watchdog.observe(make_chunk(camera_id, index), GOOD if kind == "g" else BAD)


def at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


async def test_changes_are_published_once_with_the_payload() -> None:
    bus = Bus()
    task = asyncio.create_task(_collect(bus, 2))
    await asyncio.sleep(0)
    watchdog = CoverageWatchdog(bus)
    feed(watchdog, "gbbbggg")
    messages = await asyncio.wait_for(task, timeout=1)
    assert [m.payload for m in messages] == [
        {
            "camera_id": "cam1",
            "state": "blind",
            "reason": "blocked",
            "moved": False,
            "ts": "2026-10-30T12:00:09Z",
        },
        {
            "camera_id": "cam1",
            "state": "ok",
            "reason": None,
            "moved": False,
            "ts": "2026-10-30T12:00:18Z",
        },
    ]
    assert watchdog.status("cam1").state is CoverageState.OK


async def test_moved_camera_publishes_moved_flag_and_stays_blind_until_recalibrated() -> None:
    bus = Bus()
    task = asyncio.create_task(_collect(bus, 2))
    await asyncio.sleep(0)
    watchdog = CoverageWatchdog(bus)
    watchdog.set_reference("cam1", make_reference(BASE))
    moved_view = chunk_of(np.roll(BASE, 64, axis=1))
    results = [watchdog.observe(make_chunk("cam1", i), moved_view) for i in range(3)]
    assert [r.changed for r in results] == [False, True, False]
    assert results[1].reason is CoverageReason.MOVED

    watchdog.set_reference("cam1", make_reference(moved_view[0]))
    for i in range(3, 5):
        watchdog.observe(make_chunk("cam1", i), moved_view)
    messages = await asyncio.wait_for(task, timeout=1)
    assert [(m.payload["state"], m.payload["moved"]) for m in messages] == [  # type: ignore[index]
        ("blind", True),
        ("ok", False),
    ]


def test_watchdog_without_bus_still_tracks_state() -> None:
    watchdog = CoverageWatchdog()
    feed(watchdog, "bb")
    assert watchdog.status("cam1").status is CoverageReason.BLOCKED
    assert watchdog.status("cam2").state is CoverageState.OK


def test_watched_fraction_arithmetic() -> None:
    watchdog = CoverageWatchdog()
    feed(watchdog, "ggbbbggggg")  # chunks 2-4 bad, chunk 5 still pending recovery
    assert [s.watched for s in watchdog.segments("cam1")] == [
        True, True, False, False, False, False, True, True, True, True,
    ]  # fmt: skip
    assert watchdog.watched_fraction("cam1", at(0), at(30)) == pytest.approx(18 / 30)
    assert watchdog.watched_fraction("cam1", at(3), at(15)) == pytest.approx(3 / 12)
    assert watchdog.watched_fraction("cam1", at(0), at(60)) == pytest.approx(18 / 60)
    assert watchdog.watched_fraction("cam1", at(-30), at(0)) == 0.0
    assert watchdog.watched_fraction("unknown", at(0), at(30)) == 0.0


def test_blind_intervals_include_unobserved_time() -> None:
    watchdog = CoverageWatchdog()
    feed(watchdog, "ggbbbggggg")
    blind = watchdog.blind_intervals("cam1", at(0), at(60))
    assert [(b.start, b.end) for b in blind] == [(at(6), at(18)), (at(30), at(60))]


def test_window_must_have_positive_length() -> None:
    watchdog = CoverageWatchdog()
    with pytest.raises(ValueError, match="must be after"):
        watchdog.watched_fraction("cam1", at(5), at(5))
    with pytest.raises(ValueError, match="must be after"):
        watchdog.blind_intervals("cam1", at(5), at(1))


def test_chunks_must_arrive_in_order() -> None:
    watchdog = CoverageWatchdog()
    watchdog.observe(make_chunk("cam1", 3), GOOD)
    with pytest.raises(ValueError, match="starts before"):
        watchdog.observe(make_chunk("cam1", 1), GOOD)


@settings(max_examples=25, deadline=None)
@given(st.lists(st.booleans(), min_size=1, max_size=12))
def test_watched_and_blind_time_partition_the_window(pattern: list[bool]) -> None:
    watchdog = CoverageWatchdog()
    feed(watchdog, "".join("g" if good else "b" for good in pattern))
    t0, t1 = at(-3), at(3 * len(pattern) + 3)
    window_s = (t1 - t0).total_seconds()
    fraction = watchdog.watched_fraction("cam1", t0, t1)
    blind_s = sum(b.seconds for b in watchdog.blind_intervals("cam1", t0, t1))
    assert 0.0 <= fraction <= 1.0
    assert fraction * window_s + blind_s == pytest.approx(window_s)


# --- compliance accounting --------------------------------------------------------------------


def outcome(start: float, end: float, *, compliant: bool, camera_id: str = "cam1") -> RuleOutcome:
    return RuleOutcome(
        rule_id="wash-before-line",
        camera_id=camera_id,
        start=at(start),
        end=at(end),
        compliant=compliant,
    )


def candidate(start: float, end: float) -> CandidateEvent:
    return CandidateEvent(
        event_id="ev-1",
        rule_id="wash-before-line",
        camera_id="cam1",
        ts_start=at(start),
        ts_end=at(end),
        severity=Severity.MEDIUM,
        predicate_trace={},
        strip_paths=(),
        track_window_hash="h",
        plan_version_hash="p",
        rule_ir_hash="r",
    )


def test_blind_interval_is_excluded_from_compliance_counts() -> None:
    watchdog = CoverageWatchdog()
    feed(watchdog, "ggbbbggggg")  # blind 6 s - 18 s
    blind = {"cam1": watchdog.blind_intervals("cam1", at(0), at(30))}
    outcomes = [
        outcome(1, 2, compliant=True),  # watched
        outcome(10, 11, compliant=True),  # inside BLIND: never compliant
        outcome(17, 19, compliant=True),  # straddles the end of BLIND
        outcome(7, 8, compliant=False),  # a violation seen through a blocked view
        outcome_from_candidate(candidate(20, 21)),  # watched violation from the engine
        outcome(12, 13, compliant=True, camera_id="cam2"),  # another camera, no blind time
    ]
    tally = tally_compliance(outcomes, blind)
    assert (tally.compliant, tally.violations, tally.blind) == (2, 1, 3)
    assert tally.rate == pytest.approx(2 / 3)


def test_instant_outcomes_and_empty_tally() -> None:
    blind = [Interval(start=at(5), end=at(10))]
    assert classify_outcome(outcome(5, 5, compliant=True), blind) is OutcomeStatus.BLIND
    assert classify_outcome(outcome(10, 10, compliant=True), blind) is OutcomeStatus.COMPLIANT
    assert tally_compliance([], {}).rate is None


def test_outcome_rejects_reversed_span() -> None:
    with pytest.raises(ValueError, match="ends before"):
        outcome(2, 1, compliant=True)


# --- reading chunks from disk: a tray over the lens -------------------------------------------


def write_chunk(path: Path, view: Frame, n_frames: int = 30, sigma: float = 3.0) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"MJPG"), 10.0, (WIDTH, HEIGHT))
    for seed in range(n_frames):
        writer.write(live(view, seed, sigma))
    writer.release()


def disk_chunk(tmp_path: Path, index: int, view: Frame) -> Chunk:
    path = tmp_path / f"cam1_{index:06d}.avi"
    write_chunk(path, view)
    return make_chunk("cam1", index).model_copy(update={"path": str(path)})


def test_read_frames_samples_evenly(tmp_path: Path) -> None:
    path = tmp_path / "clip.avi"
    write_chunk(path, BASE)
    frames = read_frames(path, n_frames=30, max_frames=8)
    assert len(frames) == 8
    assert frames[0].shape == (HEIGHT, WIDTH, 3)


def test_read_frames_rejects_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot open"):
        read_frames(tmp_path / "missing.avi", n_frames=30)
    broken = tmp_path / "broken.avi"
    broken.write_bytes(b"not a video")
    with pytest.raises(ValueError, match=r"cannot open|no frames"):
        read_frames(broken, n_frames=30)


def test_tray_over_the_camera_flips_to_blind_within_two_chunks(tmp_path: Path) -> None:
    watchdog = CoverageWatchdog()
    covered = tray(BASE, 0.75)
    views = [BASE, BASE, covered, covered, BASE, BASE]
    verdicts = [watchdog.observe_chunk(disk_chunk(tmp_path, i, v)) for i, v in enumerate(views)]
    assert [v.raw for v in verdicts] == [None, None, B, B, None, None]
    assert [v.state for v in verdicts] == [
        CoverageState.OK, CoverageState.OK, CoverageState.OK,
        CoverageState.BLIND, CoverageState.BLIND, CoverageState.OK,
    ]  # fmt: skip
    assert watchdog.watched_fraction("cam1", at(0), at(18)) == pytest.approx(9 / 18)
