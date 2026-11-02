"""Calibration from a MoGe-2 point map (add-on 3.5): the worker, the client and the plane fit."""

from __future__ import annotations

import base64
import math
from collections.abc import Iterator
from typing import Any

import httpx
import numpy as np
import pytest

from rulecam.clients.transport import HttpxTransport
from rulecam.perception.calibration import (
    MOGE_REFERENCE,
    calibrate_point_map,
    fit_floor_plane,
    ground_distance_m,
    person_scale_ratio,
)
from rulecam.perception.errors import CalibrationError, SamResponseError, SamRuntimeUnavailableError
from rulecam.perception.geometry_worker import (
    DEFAULT_MOGE_MODEL,
    MogeEngine,
    check_geometry_request,
    grid_shape,
    sample_grid,
)
from rulecam.perception.point_map import WorkerGeometry, grid_mask, parse_point_map
from rulecam.perception.sam_runtime import HttpSamWorker
from rulecam.perception.sam_server import GEOMETRY_PATH
from tests.perception.test_sam_server import KEY, FakeEngine, Running

W, H, FOCAL = 1280, 720, 900.0
K = np.array([[FOCAL, 0.0, W / 2], [0.0, FOCAL, H / 2], [0.0, 0.0, 1.0]])


def up_vector(pitch_deg: float) -> np.ndarray:
    """World up in OpenCV camera axes, for a level camera pitched down by `pitch_deg`."""
    pitch = math.radians(pitch_deg)
    return np.array([0.0, -math.cos(pitch), -math.sin(pitch)])


def synthetic_point_map(
    *,
    height_m: float = 3.0,
    pitch_deg: float = 25.0,
    rows: int = 54,
    cols: int = 96,
    width: int = W,
    height: int = H,
    intrinsics: np.ndarray = K,
) -> np.ndarray:
    """What MoGe-2 would return for a camera over an empty floor: NaN above the horizon."""
    up = up_vector(pitch_deg)
    points = np.full((rows, cols, 3), np.nan)
    for r in range(rows):
        for c in range(cols):
            ray = np.linalg.solve(
                intrinsics, [(c + 0.5) * width / cols, (r + 0.5) * height / rows, 1]
            )
            if (denom := float(ray @ up)) < -1e-9:
                points[r, c] = (-height_m / denom) * ray
    return points


def pixel(point: np.ndarray, intrinsics: np.ndarray = K) -> tuple[float, float]:
    projected = intrinsics @ point
    return float(projected[0] / projected[2]), float(projected[1] / projected[2])


# --- the plane fit and the homography ---------------------------------------------------------


def test_a_point_map_calibration_measures_true_floor_distances() -> None:
    points = synthetic_point_map()
    calibration = calibrate_point_map("cam1", points, np.isfinite(points).all(-1), K)
    assert calibration.reference == MOGE_REFERENCE
    assert calibration.method == "reference_object"
    assert calibration.err_m == pytest.approx(0.08, abs=1e-3)  # flat floor: only the scale term
    up = up_vector(25.0)
    forward = np.cross(up, np.array([1.0, 0.0, 0.0]))
    forward *= np.sign(forward[2])  # along the floor, away from the camera
    start = -3.0 * up + 6.0 * forward
    across, ahead = start + np.array([2.0, 0.0, 0.0]), start + 3.0 * forward
    assert ground_distance_m(calibration, pixel(start), pixel(across)) == pytest.approx(2.0)
    assert ground_distance_m(calibration, pixel(start), pixel(ahead)) == pytest.approx(3.0)
    head, foot = pixel(start + 1.7 * up), pixel(start)
    tall = foot[1] - head[1]
    person = (foot[0] - tall / 5, head[1], foot[0] + tall / 5, foot[1])
    assert person_scale_ratio(calibration, person) == pytest.approx(1.0, abs=0.02)


def test_the_floor_mask_keeps_walls_and_clutter_out_of_the_fit() -> None:
    points = synthetic_point_map()
    floor = np.isfinite(points).all(-1)
    cluttered = points.copy()
    cluttered[-10:, :20] += np.array([0.0, -1.0, 0.0])  # a shelf 1 m above the floor
    with_shelf = calibrate_point_map("cam1", cluttered, floor, K)
    floor_only = floor.copy()
    floor_only[-10:, :20] = False
    clean = calibrate_point_map("cam1", cluttered, floor_only, K)
    assert np.allclose(clean.homography, calibrate_point_map("cam1", points, floor, K).homography)
    assert with_shelf.err_m >= clean.err_m  # RANSAC drops the shelf, the residual stays honest


def test_fit_floor_plane_orients_the_normal_towards_the_camera_and_drops_outliers() -> None:
    rng = np.random.default_rng(3)
    floor = np.column_stack([rng.uniform(-3, 3, 200), np.full(200, 2.0), rng.uniform(2, 9, 200)])
    noise = np.column_stack([rng.uniform(-3, 3, 20), rng.uniform(-1, 1, 20), rng.uniform(2, 9, 20)])
    normal, offset, inliers = fit_floor_plane(np.vstack([floor, noise]), seed=1)
    assert offset == pytest.approx(2.0)
    assert normal == pytest.approx(np.array([0.0, -1.0, 0.0]), abs=1e-9)
    assert inliers[:200].all()
    assert inliers[200:].sum() < 5


@pytest.mark.parametrize(
    ("points", "floor", "match"),
    [
        (np.zeros((4, 4, 2)), np.ones((4, 4), bool), "rows, cols, 3"),
        (synthetic_point_map(), np.zeros((54, 96), bool), "floor points"),
        (synthetic_point_map(height_m=60.0), None, "implausible"),
    ],
)
def test_bad_point_maps_are_refused(points: np.ndarray, floor: Any, match: str) -> None:
    mask = np.isfinite(points).all(-1) if floor is None else floor
    with pytest.raises(CalibrationError, match=match):
        calibrate_point_map("cam1", points, mask, K)


# --- the GPU side (`geometry_worker`) ---------------------------------------------------------


def test_sample_grid_takes_cell_centres_and_drops_invalid_points() -> None:
    points = np.arange(4 * 6 * 3, dtype=np.float64).reshape(4, 6, 3)
    mask = np.ones((4, 6), bool)
    mask[0, 1] = False
    points[3, 5] = np.inf
    shape, cells = sample_grid(points, mask, cells=6)
    assert shape == (4, 6)
    assert cells[1] is None  # masked
    assert cells[-1] is None  # not finite
    assert cells[0] == [0.0, 1.0, 2.0]
    assert grid_shape(1280, 720, 96) == (54, 96)
    assert grid_shape(720, 1280, 96) == (96, 54)


def test_geometry_requests_and_engine_state() -> None:
    assert check_geometry_request({}) == 96
    for bad in ({"grid": 4}, {"grid": 999}, {"grid": "96"}, {"grid": True}):
        with pytest.raises(ValueError, match="grid"):
            check_geometry_request(bad)
    engine = MogeEngine()
    stats = engine.memory_stats()
    assert stats["model"] == DEFAULT_MOGE_MODEL
    assert stats["model_loaded"] is False
    assert stats["available"] is MogeEngine.available()


# --- the route, the client and the parsed point map --------------------------------------------


def geometry_reply(points: np.ndarray, width: int = W, height: int = H) -> dict[str, Any]:
    rows, cols = points.shape[:2]
    normalised = np.diag([1 / width, 1 / height, 1.0]) @ K
    return {
        "model": "fake-moge",
        "width": width,
        "height": height,
        "intrinsics": normalised.tolist(),
        "grid": [rows, cols],
        "points": [
            None if not np.isfinite(p).all() else [float(v) for v in p]
            for p in points.reshape(-1, 3)
        ],
        "gpu_seconds": 0.4,
    }


class FakeGeometry:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, dict[str, Any]]] = []

    def estimate(self, image: bytes, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((image, request))
        return geometry_reply(synthetic_point_map(rows=9, cols=16))

    def memory_stats(self) -> dict[str, Any]:
        return {"model": "fake-moge", "available": True, "model_loaded": True, "calls": 0}


@pytest.fixture
def geometry() -> Iterator[Running]:
    server = Running(FakeEngine(), geometry_engine=FakeGeometry())
    yield server
    server.close()


def test_point_map_round_trip_through_the_server(geometry: Running) -> None:
    estimator = WorkerGeometry(HttpSamWorker(HttpxTransport(geometry.url, KEY)), grid=16)
    assert estimator.available()
    point_map = estimator.estimate(b"\xff\xd8frame", camera_id="dock")
    assert point_map.points.shape == (9, 16, 3)
    assert np.allclose(point_map.intrinsics_px, K)
    assert point_map.hfov_deg == pytest.approx(math.degrees(2 * math.atan(W / (2 * FOCAL))))
    image, request = geometry.server.geometry_engine.calls[0]  # type: ignore[union-attr]
    assert (image, request) == (b"\xff\xd8frame", {"grid": 16})
    bad = {"request": {"grid": 2}, "image_b64": base64.b64encode(b"x").decode()}
    reply = httpx.post(
        f"{geometry.url}{GEOMETRY_PATH}", json=bad, headers={"Authorization": f"Bearer {KEY}"}
    )
    assert reply.status_code == 400


def test_auto_mode_asks_health_once_and_never_uses_a_pod_without_moge() -> None:
    server = Running(FakeEngine())
    try:
        worker = HttpSamWorker(HttpxTransport(server.url, KEY))
        auto = WorkerGeometry(worker)
        assert auto.available() is False
        assert auto.available() is False
        reply = httpx.post(
            f"{server.url}{GEOMETRY_PATH}",
            json={"request": {}, "image_b64": "aGk="},
            headers={"Authorization": f"Bearer {KEY}"},
        )
        assert reply.status_code == 404
        assert WorkerGeometry(worker, check_health=False).available() is True
    finally:
        server.close()


class Unreachable:
    def health(self) -> dict[str, Any]:
        raise SamRuntimeUnavailableError("pod asleep")

    def estimate_geometry(self, image: bytes, request: dict[str, Any]) -> dict[str, Any]:
        return {"grid": [1, 1]}


def test_a_failed_health_call_is_asked_again_and_malformed_maps_are_refused() -> None:
    estimator = WorkerGeometry(Unreachable())
    assert estimator.available() is False
    with pytest.raises(SamResponseError):
        estimator.estimate(b"", camera_id="c")
    reply = geometry_reply(synthetic_point_map(rows=2, cols=2))
    reply["points"] = reply["points"][:3]
    with pytest.raises(SamResponseError, match="2 x 2"):
        parse_point_map(reply)


def test_grid_mask_samples_a_full_size_mask_like_the_pod() -> None:
    mask = np.zeros((H, W), np.uint8)
    mask[H // 2 :] = 1
    sampled = grid_mask(mask, 54, 96)
    assert sampled.shape == (54, 96)
    assert not sampled[:27].any()
    assert sampled[27:].all()


def test_the_install_script_pins_the_model_the_worker_loads() -> None:
    from pathlib import Path  # noqa: PLC0415 - one test

    script = Path(__file__).parents[2] / "scripts" / "sam_pod" / "install_moge.sh"
    text = script.read_text("utf-8")
    assert DEFAULT_MOGE_MODEL in text
    assert "--no-deps" in text  # never touch the SAM venv's numpy < 2, torch or OpenCV
