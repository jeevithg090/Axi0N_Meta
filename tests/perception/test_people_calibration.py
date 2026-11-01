"""Ground calibration from standing people, and the people check on other calibrations."""

from __future__ import annotations

import math

import numpy as np
import pytest

from rulecam.contracts import Calibration
from rulecam.contracts.core import BoxXYXY
from rulecam.perception.calibration import (
    PERSON_HEIGHT_M,
    PERSON_REFERENCE,
    calibrate_people,
    ground_distance_m,
    people_agree,
    person_scale_ratio,
)
from rulecam.perception.errors import CalibrationError

WIDTH, HEIGHT = 1280, 720
FOCAL = WIDTH / (2.0 * math.tan(math.radians(65.0) / 2.0))  # the fit's assumed lens
CAMERA_HEIGHT_M = 3.0


def project(x_m: float, up_m: float, z_m: float, pitch_deg: float) -> tuple[float, float]:
    """Pixel of a world point (across, up, forward) for a camera tilted down by `pitch_deg`."""
    pitch = math.radians(pitch_deg)
    dy = up_m - CAMERA_HEIGHT_M
    depth = -dy * math.sin(pitch) + z_m * math.cos(pitch)
    down = -dy * math.cos(pitch) - z_m * math.sin(pitch)
    return WIDTH / 2 + FOCAL * x_m / depth, HEIGHT / 2 + FOCAL * down / depth


def person_box(x_m: float, z_m: float, pitch_deg: float) -> BoxXYXY:
    """Box of a 1.7 m tall, 0.5 m wide person standing at (x_m, z_m)."""
    left, foot = project(x_m - 0.25, 0.0, z_m, pitch_deg)
    right, _ = project(x_m + 0.25, 0.0, z_m, pitch_deg)
    _, head = project(x_m, PERSON_HEIGHT_M, z_m, pitch_deg)
    return (left, head, right, foot)


def crowd(pitch_deg: float) -> list[BoxXYXY]:
    spots = [(-2.0, 5.0), (1.0, 6.0), (3.0, 8.0), (-1.0, 9.0), (0.5, 11.0), (2.0, 13.0)]
    return [person_box(x, z, pitch_deg) for x, z in spots]


def ground_px(x_m: float, z_m: float, pitch_deg: float) -> tuple[float, float]:
    return project(x_m, 0.0, z_m, pitch_deg)


@pytest.mark.parametrize("pitch_deg", [0.0, 15.0, 30.0])
def test_people_give_the_scale_across_the_view(pitch_deg: float) -> None:
    cal = calibrate_people("dock", crowd(pitch_deg), WIDTH, HEIGHT)

    assert (cal.method, cal.reference) == ("reference_object", PERSON_REFERENCE)
    for z_m in (6.0, 9.0):
        a, b = ground_px(-1.0, z_m, pitch_deg), ground_px(1.0, z_m, pitch_deg)
        assert ground_distance_m(cal, a, b) == pytest.approx(2.0, rel=0.15)


def test_people_give_the_depth_too_on_a_level_camera() -> None:
    cal = calibrate_people("dock", crowd(0.0), WIDTH, HEIGHT)
    near, far = ground_px(0.0, 6.0, 0.0), ground_px(0.0, 8.0, 0.0)
    assert ground_distance_m(cal, near, far) == pytest.approx(2.0, rel=0.15)


def test_people_at_one_depth_do_not_calibrate() -> None:
    boxes = [person_box(x, 7.0, 20.0) for x in (-2.0, 0.0, 2.0)]
    with pytest.raises(CalibrationError, match="one depth"):
        calibrate_people("dock", boxes, WIDTH, HEIGHT)


def test_too_few_people_do_not_calibrate() -> None:
    with pytest.raises(CalibrationError, match="standing people"):
        calibrate_people("dock", crowd(20.0)[:2], WIDTH, HEIGHT)


def scaled(cal: Calibration, factor: float) -> Calibration:
    """The same calibration with every ground distance multiplied by `factor`."""
    matrix = np.diag([factor, factor, 1.0]) @ np.asarray(cal.homography)
    rows = tuple((float(r[0]), float(r[1]), float(r[2])) for r in matrix)
    return cal.model_copy(update={"homography": rows})


def test_a_calibration_four_times_too_large_disagrees_with_the_people() -> None:
    people = crowd(20.0)
    right = calibrate_people("dock", people, WIDTH, HEIGHT)

    assert person_scale_ratio(right, person_box(0.0, 7.0, 20.0)) == pytest.approx(1.0, rel=0.2)
    assert people_agree(right, people)
    assert not people_agree(scaled(right, 4.0), people)
    assert not people_agree(scaled(right, 0.25), people)


def test_without_enough_people_any_calibration_is_accepted() -> None:
    right = calibrate_people("dock", crowd(20.0), WIDTH, HEIGHT)
    assert people_agree(scaled(right, 4.0), crowd(20.0)[:2])


def test_a_box_without_height_is_rejected() -> None:
    cal = calibrate_people("dock", crowd(20.0), WIDTH, HEIGHT)
    with pytest.raises(CalibrationError, match="positive width and height"):
        person_scale_ratio(cal, (10.0, 50.0, 20.0, 50.0))
