"""One crowd reading: SAM on tiles, density per square metre, motion and the heatmap."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rulecam.ask.vision import Frame
from rulecam.crowd.scan import VIEW, CrowdScanner
from rulecam.crowd.zones import CrowdZone
from tests.crowd.conftest import SHIFT_PX_PER_FRAME, Sam, top_down
from tests.fakes import EPOCH

PEOPLE = [(x, y, x + 20.0, y + 50.0) for x in (20.0, 120.0, 220.0) for y in (40.0, 160.0)]


def test_a_calibrated_reading_measures_density_motion_and_zones(
    tmp_path: Path, frame_at: Callable[[int, datetime], Frame]
) -> None:
    scanner = CrowdScanner(segmenter=Sam(PEOPLE), out_dir=tmp_path / "heat", grid=1)
    before = frame_at(0, EPOCH)
    after = frame_at(5, EPOCH + timedelta(seconds=0.5))
    left = CrowdZone(name="west gate", polygon_px=[(0, 0), (200, 0), (200, 300), (0, 300)])
    reading = scanner.scan(after, before, (top_down(0.05), "plan"), [left])
    view = reading.zone(VIEW)
    assert view is not None
    assert reading.people == view.people == 6
    assert view.area_m2 == pytest.approx(300.0)  # 20 m x 15 m seen from above
    assert view.density_ppm2 == pytest.approx(0.02)
    shift_m = 5 * SHIFT_PX_PER_FRAME * 0.05
    assert view.speed_mps == pytest.approx(shift_m / 0.5, abs=0.1)
    assert view.coherence == pytest.approx(1.0, abs=0.05)
    assert view.pressure is not None
    gate = reading.zone("west gate")
    assert gate is not None
    assert gate.people == 4
    assert gate.area_m2 == pytest.approx(150.0, rel=0.05)
    assert reading.calibration == "plan"
    assert len(reading.cells) == 48
    assert reading.peak_density_ppm2 == pytest.approx(1 / 6.25)  # one person in a 2.5 m cell
    assert reading.heatmap is not None
    assert (tmp_path / "heat" / reading.heatmap).is_file()


def test_without_a_calibration_only_counts_and_pixel_speeds(
    tmp_path: Path, frame_at: Callable[[int, datetime], Frame]
) -> None:
    scanner = CrowdScanner(segmenter=Sam(PEOPLE), out_dir=tmp_path, grid=1)
    reading = scanner.scan(frame_at(1, EPOCH), None, None)
    view = reading.zone(VIEW)
    assert view is not None
    assert (reading.people, view.density_ppm2, view.speed_mps) == (6, None, None)
    assert reading.calibration == "none"
    assert reading.cells == []
    assert reading.notes


def test_tiles_are_counted_once_each(
    tmp_path: Path, frame_at: Callable[[int, datetime], Frame]
) -> None:
    sam = Sam([(10.0, 10.0, 30.0, 60.0)])  # one person near each tile's top-left corner
    scanner = CrowdScanner(segmenter=sam, out_dir=tmp_path, concept="head", grid=2)
    reading = scanner.scan(frame_at(1, EPOCH), None, None)
    assert reading.people == 4
    assert [concept for _, concept in sam.calls] == ["head"] * 4
    assert not list((tmp_path / "tiles").glob("*.jpg"))  # tiles are removed afterwards


def test_an_unreadable_frame_raises(tmp_path: Path) -> None:
    scanner = CrowdScanner(segmenter=Sam([]), out_dir=tmp_path)
    missing = Frame(camera_id="gate", at=EPOCH, path=tmp_path / "missing.jpg")
    with pytest.raises(OSError, match="cannot read"):
        scanner.scan(missing, None, None)
    with pytest.raises(OSError, match="cannot read"):
        scanner.detect(missing, "person", 2)
