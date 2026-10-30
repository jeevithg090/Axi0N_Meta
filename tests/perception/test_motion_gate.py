from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from rulecam.contracts import Severity
from rulecam.perception.errors import PerceptionError
from rulecam.perception.metrics import PerceptionMetrics
from rulecam.perception.motion_gate import (
    GateConfig,
    MotionGate,
    MotionStats,
    decide,
    downscale_gray,
    dynamic_concepts,
    is_still,
    motion_stats,
    protected_concepts,
    read_gray_frames,
)
from tests.fakes import EPOCH, make_chunk
from tests.perception.helpers import make_rule, moving_square_frames, still_frames

CONFIG = GateConfig()


def test_static_video_is_still() -> None:
    stats = motion_stats(still_frames())
    assert stats == MotionStats(mean_abs_diff=0.0, changed_fraction=0.0)
    assert is_still(stats, CONFIG)


def test_moving_square_is_not_still() -> None:
    stats = motion_stats(moving_square_frames())
    assert stats.changed_fraction > CONFIG.max_changed_fraction
    assert not is_still(stats, CONFIG)


def test_sensor_noise_below_pixel_delta_stays_still() -> None:
    rng = np.random.default_rng(7)
    base = np.full((90, 160), 90, dtype=np.int16)
    frames = [np.clip(base + rng.integers(-1, 2, base.shape), 0, 255).astype(np.uint8)]
    frames += [frames[0].copy() for _ in range(5)]
    frames[3] = np.clip(base + rng.integers(-1, 2, base.shape), 0, 255).astype(np.uint8)
    stats = motion_stats(frames)
    assert stats.changed_fraction == 0.0
    assert is_still(stats, CONFIG)


def test_one_moving_pair_is_enough() -> None:
    frames = still_frames(10)
    frames[6] = moving_square_frames(1)[0]
    assert not is_still(motion_stats(frames), CONFIG)


def test_motion_needs_two_frames() -> None:
    with pytest.raises(ValueError, match="two frames"):
        motion_stats(still_frames(1))


def test_unknown_motion_is_never_still() -> None:
    assert not is_still(None, CONFIG)


def test_downscale_gray_keeps_aspect_and_converts_bgr() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    gray = downscale_gray(frame)
    assert gray.shape == (90, 160)
    assert gray.dtype == np.uint8
    assert downscale_gray(np.zeros((480, 640), dtype=np.uint8)).shape == (120, 160)


def test_concept_sets() -> None:
    rules = [
        make_rule("ppe", dynamic=("person", "hard hat"), severity=Severity.HIGH),
        make_rule("wash", dynamic=("person", "hands"), static=("sink",)),
    ]
    assert dynamic_concepts(rules) == {"person", "hard hat", "hands"}
    assert protected_concepts(rules) == {"person", "hard hat"}


def test_still_chunk_skips_dynamic_concepts() -> None:
    rules = [make_rule(dynamic=("person", "hands"), static=("sink",))]
    decision = decide(
        rules,
        motion_stats(still_frames()),
        chunk_start=EPOCH + timedelta(seconds=3),
        last_full_run=EPOCH,
        config=CONFIG,
    )
    assert decision.still
    assert not decision.keyframe
    assert decision.run == ()
    assert decision.skipped == ("hands", "person")


def test_moving_chunk_runs_every_dynamic_concept() -> None:
    rules = [make_rule(dynamic=("person", "hands"))]
    decision = decide(
        rules,
        motion_stats(moving_square_frames()),
        chunk_start=EPOCH + timedelta(seconds=3),
        last_full_run=EPOCH,
        config=CONFIG,
    )
    assert decision.run == ("hands", "person")
    assert decision.skipped == ()


def test_high_severity_concept_is_never_gated() -> None:
    rules = [
        make_rule("interlock", dynamic=("person", "forklift"), severity=Severity.HIGH),
        make_rule("tidy", dynamic=("box",), severity=Severity.LOW),
    ]
    decision = decide(
        rules,
        motion_stats(still_frames()),
        chunk_start=EPOCH + timedelta(seconds=3),
        last_full_run=EPOCH,
        config=CONFIG,
    )
    assert decision.run == ("forklift", "person")
    assert decision.skipped == ("box",)


def test_first_chunk_is_a_keyframe() -> None:
    decision = decide(
        [make_rule()],
        motion_stats(still_frames()),
        chunk_start=EPOCH,
        last_full_run=None,
        config=CONFIG,
    )
    assert decision.keyframe
    assert decision.run == ("person",)


def test_keyframe_cadence_holds_on_a_static_scene() -> None:
    metrics = PerceptionMetrics()
    gate = MotionGate(metrics=metrics)
    rules = [make_rule(dynamic=("person", "hands"))]
    full_runs = []
    for index in range(40):  # 40 chunks of 3 s = 120 s of a still scene
        chunk = make_chunk(index=index)
        decision = gate.decide(chunk, rules, frames=still_frames())
        if decision.run:
            full_runs.append((chunk.start_ts - EPOCH).total_seconds())
    assert full_runs == [0.0, 30.0, 60.0, 90.0]
    camera = metrics.report().cameras[0]
    assert camera.camera_hours == pytest.approx(120 / 3600)
    assert camera.gated_fraction == pytest.approx(72 / 80)


def test_motion_resets_the_keyframe_clock() -> None:
    gate = MotionGate()
    rules = [make_rule()]
    runs = []
    for index in range(16):
        frames = moving_square_frames() if index == 5 else still_frames()
        decision = gate.decide(make_chunk(index=index), rules, frames=frames)
        runs.append(bool(decision.run))
    expected = [i in (0, 5, 6) for i in range(16)]  # chunk 6 still sees motion at its boundary
    assert runs == expected


def test_motion_across_the_chunk_boundary_counts() -> None:
    gate = MotionGate()
    rules = [make_rule()]
    gate.decide(make_chunk(index=0), rules, frames=still_frames(level=90))
    decision = gate.decide(make_chunk(index=1), rules, frames=still_frames(level=200))
    assert not decision.still
    assert decision.run == ("person",)


def test_single_frame_chunk_is_not_gated() -> None:
    decision = MotionGate().decide(make_chunk(n_frames=1), [make_rule()], frames=still_frames(1))
    assert decision.stats is None
    assert decision.run == ("person",)


def test_gate_reads_and_downscales_the_chunk_file(tmp_path: Path) -> None:
    path = tmp_path / "cam1_000000.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), 10, (320, 180))
    for i in range(10):
        frame = np.full((180, 320, 3), 90, dtype=np.uint8)
        frame[60:100, 20 + 20 * i : 60 + 20 * i] = 255
        writer.write(frame)
    writer.release()

    frames = read_gray_frames(path)
    assert len(frames) == 10
    assert frames[0].shape == (90, 160)
    chunk = make_chunk(n_frames=10).model_copy(update={"path": str(path)})
    decision = MotionGate().decide(chunk, [make_rule()])
    assert decision.stats is not None
    assert not decision.still


def test_unreadable_video_raises(tmp_path: Path) -> None:
    with pytest.raises(PerceptionError, match="cannot open"):
        read_gray_frames(tmp_path / "missing.mp4")
