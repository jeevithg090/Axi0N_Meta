"""Crowd memory: readings every few seconds, calibration from the plan, MoGe-2 or the crowd."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rulecam.ask.vision import Frame, FrameGrabber
from rulecam.contracts import Calibration
from rulecam.crowd.memory import Calibrations, CrowdMemory
from rulecam.crowd.scan import CrowdScanner
from rulecam.crowd.zones import CrowdZone
from tests.crowd.conftest import Sam, top_down, wait_for
from tests.fakes import EPOCH, FakeClock

# Standing people at three depths, taller nearer the camera: a ruler for `calibrate_people`.
STANDING = [
    (40.0 + 60 * k, top, 40.0 + 60 * k + h / 3, top + h)
    for k, (top, h) in enumerate(
        [(60.0, 50.0), (70.0, 60.0), (90.0, 75.0), (110.0, 95.0), (130.0, 120.0)]
    )
]


def memory(tmp_path: Path, grabber: FrameGrabber, calibrations: Calibrations) -> CrowdMemory:
    return CrowdMemory(
        frames=grabber,
        scanner=CrowdScanner(segmenter=Sam(STANDING), out_dir=tmp_path / "heat", grid=1),
        calibrations=calibrations,
        path=tmp_path / "crowd" / "readings.jsonl",
        every_s=5.0,
        zones=lambda camera: [
            CrowdZone(name="gate", polygon_px=[(0, 0), (200, 0), (200, 300), (0, 300)])
        ],
    )


def test_a_tick_records_one_row_per_zone(tmp_path: Path, grabber: FrameGrabber) -> None:
    plan = top_down(0.05)
    crowd = memory(tmp_path, grabber, Calibrations(plan=lambda: {"gate": plan}))
    crowd.tick()
    wait_for(lambda: crowd.latest("gate") is not None)
    latest = crowd.latest("gate")
    assert latest is not None
    assert latest.calibration == "plan"
    assert latest.zones[0].speed_mps is not None  # motion from the frame half a second before
    rows = [json.loads(line) for line in crowd.path.read_text().splitlines()]
    assert [r["zone"] for r in rows] == ["view", "gate"]
    assert rows[0]["people"] == 5
    assert crowd.cameras() == ["gate"]
    assert crowd.readings("gate") == [latest]
    assert crowd.readings("gate", since=latest.at) == []
    crowd.tick()  # nothing new is due: the newest video was already read
    assert len(crowd.readings("gate")) == 1


def test_measure_raises_without_a_recorded_frame(tmp_path: Path, grabber: FrameGrabber) -> None:
    crowd = memory(tmp_path, grabber, Calibrations())
    with pytest.raises(ValueError, match="no recorded frame"):
        crowd.measure("gate", EPOCH - timedelta(hours=1))


def test_the_crowd_itself_calibrates_when_nothing_else_does(
    frame_at: Callable[[int, datetime], Frame],
) -> None:
    frame = frame_at(1, EPOCH)
    boxes = [(*box, 0.9) for box in STANDING]
    found = Calibrations().get("gate", frame, boxes)
    assert found is not None
    assert found[1] == "people"


def test_moge2_wins_over_the_crowd_and_failures_wait_before_a_retry(
    frame_at: Callable[[int, datetime], Frame],
) -> None:
    frame = frame_at(1, EPOCH)
    calls: list[str] = []
    answers: list[Calibration | None] = [None, top_down()]

    def model(camera_id: str, path: Path) -> Calibration | None:
        calls.append(camera_id)
        return answers.pop(0)

    now = [EPOCH]
    lookup = Calibrations(model=model, clock=lambda: now[0])
    assert lookup.get("gate", frame, []) is None  # model failed, no people to use
    assert lookup.get("gate", frame, []) is None  # not retried yet
    now[0] = EPOCH + timedelta(minutes=10)
    found = lookup.get("gate", frame, [])
    assert found is not None
    assert found[1] == "moge2"
    assert lookup.get("gate", frame, []) == found
    assert calls == ["gate", "gate"]


def test_the_plan_wins_over_everything(frame_at: Callable[[int, datetime], Frame]) -> None:
    plan = top_down(0.02)
    lookup = Calibrations(plan=lambda: {"gate": plan}, model=lambda c, p: top_down())
    assert lookup.get("gate", frame_at(1, EPOCH), []) == (plan, "plan")


def test_cameras_without_recent_video_are_skipped(tmp_path: Path, grabber: FrameGrabber) -> None:
    stale = CrowdMemory(
        frames=grabber,
        scanner=CrowdScanner(segmenter=Sam(STANDING), out_dir=tmp_path / "heat", grid=1),
        calibrations=Calibrations(),
        path=tmp_path / "readings.jsonl",
        every_s=5.0,
        clock=FakeClock(EPOCH + timedelta(hours=1)),
    )
    stale.tick()
    assert stale.cameras() == []
    assert not stale.path.exists()
