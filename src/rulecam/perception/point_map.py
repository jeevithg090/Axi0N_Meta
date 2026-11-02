"""MoGe-2 point maps from the GPU pod, for ground calibration (add-on 3.5; API side).

`WorkerGeometry` calls the pod's `POST /geometry` (`geometry_worker.MogeEngine`) and parses the
answer into a `PointMap`: the frame's size, the camera intrinsics in pixels and a grid of metric
points in camera coordinates (OpenCV axes, metres; NaN where MoGe has no valid point).
`calibration.calibrate_point_map` turns it, with SAM's floor mask sampled on the same grid
(`grid_mask`), into a ground homography.

Whether MoGe is used is the site's choice (`RULECAM_CALIBRATION_MODEL`): `auto` (the default)
asks the pod's `/health` once whether the `moge` package is installed there and uses it only if
so; `moge` always tries it (a failure falls back to the old guesses); `off` never does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, ValidationError

from rulecam.clients.errors import ModelClientError
from rulecam.contracts.core import Frozen
from rulecam.log import get_logger
from rulecam.perception.errors import SamResponseError, SamRuntimeError

_log = get_logger(__name__)

DEFAULT_GRID: Final = 96  # geometry_worker.DEFAULT_GRID (that module stays free of pydantic)

FloatArray = NDArray[np.float64]


class _PointMapWire(Frozen):
    model: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    intrinsics: tuple[tuple[float, float, float], ...] = Field(min_length=3, max_length=3)
    grid: tuple[int, int]
    points: tuple[tuple[float, float, float] | None, ...]
    gpu_seconds: float = Field(ge=0)


@dataclass(frozen=True)
class PointMap:
    """One frame's metric geometry: size, pixel intrinsics and a grid of camera-space points.

    Attributes:
        model: The model that made it.
        width: Frame width in pixels.
        height: Frame height in pixels.
        intrinsics_px: 3 x 3 camera matrix in pixels.
        points: `(rows, cols, 3)` points in metres (OpenCV camera axes), NaN where invalid.
    """

    model: str
    width: int
    height: int
    intrinsics_px: FloatArray
    points: FloatArray

    @property
    def hfov_deg(self) -> float:
        """The horizontal field of view the intrinsics imply."""
        return float(np.degrees(2 * np.arctan(self.width / (2 * self.intrinsics_px[0, 0]))))


def parse_point_map(raw: object) -> PointMap:
    """Validate the pod's `/geometry` answer.

    Raises:
        SamResponseError: Malformed, or the point count does not match the grid.
    """
    try:
        wire = _PointMapWire.model_validate(raw)
    except ValidationError as exc:
        msg = "malformed geometry response"
        raise SamResponseError(msg) from exc
    rows, cols = wire.grid
    if rows <= 0 or cols <= 0 or len(wire.points) != rows * cols:
        msg = f"geometry response has {len(wire.points)} points for a {rows} x {cols} grid"
        raise SamResponseError(msg)
    nan = (np.nan, np.nan, np.nan)
    points = np.asarray([p if p is not None else nan for p in wire.points], dtype=np.float64)
    scale = np.diag([float(wire.width), float(wire.height), 1.0])
    return PointMap(
        model=wire.model,
        width=wire.width,
        height=wire.height,
        intrinsics_px=scale @ np.asarray(wire.intrinsics, dtype=np.float64),
        points=points.reshape(rows, cols, 3),
    )


def grid_mask(mask: NDArray[np.generic], rows: int, cols: int) -> NDArray[np.bool_]:
    """Sample a full-size binary mask at the centre pixel of each grid cell (as the pod does)."""
    height, width = mask.shape[:2]
    ys = ((np.arange(rows) + 0.5) * height / rows).astype(int).clip(0, height - 1)
    xs = ((np.arange(cols) + 0.5) * width / cols).astype(int).clip(0, width - 1)
    sampled: NDArray[np.bool_] = np.asarray(mask)[np.ix_(ys, xs)] != 0
    return sampled


class GeometryEstimator(Protocol):
    """Metric geometry of one frame (`WorkerGeometry`, or a fake in tests)."""

    def available(self) -> bool:
        """Whether the model can be used at all (checked once)."""
        ...

    def estimate(self, image: bytes, *, camera_id: str) -> PointMap:
        """The frame's point map; raises when the model cannot serve it."""
        ...


class SamGeometryWorker(Protocol):
    """The pod's routes this needs (`sam_runtime.HttpSamWorker`)."""

    def estimate_geometry(self, image: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """`POST /geometry`."""
        ...

    def health(self) -> dict[str, Any]:
        """`GET /health`."""
        ...


class WorkerGeometry:
    """`GeometryEstimator` on the GPU pod's MoGe-2."""

    def __init__(
        self, worker: SamGeometryWorker, *, check_health: bool = True, grid: int = DEFAULT_GRID
    ) -> None:
        """Wrap the pod's client.

        Args:
            worker: `HttpSamWorker`.
            check_health: Use MoGe only if the pod's `/health` says it is installed (`auto`);
                false always tries it (`moge`).
            grid: Grid cells along the frame's long side.
        """
        self._worker = worker
        self._check = check_health
        self._grid = grid
        self._available: bool | None = None if check_health else True

    def available(self) -> bool:
        """Whether the pod has MoGe-2; asked once (a failed health call is asked again later)."""
        if self._available is not None:
            return self._available
        try:
            health = self._worker.health()
        except (SamRuntimeError, ModelClientError) as exc:
            _log.warning("geometry_health_failed", error=str(exc))
            return False
        geometry = health.get("geometry")
        self._available = isinstance(geometry, dict) and bool(geometry.get("available"))
        _log.info("geometry_model", available=self._available)
        return self._available

    def estimate(self, image: bytes, *, camera_id: str) -> PointMap:
        """Run MoGe-2 on one frame.

        Raises:
            SamRuntimeError: The pod could not serve it or answered malformed data.
        """
        point_map = parse_point_map(self._worker.estimate_geometry(image, {"grid": self._grid}))
        _log.info(
            "geometry_point_map",
            camera_id=camera_id,
            model=point_map.model,
            hfov_deg=round(point_map.hfov_deg, 1),
            valid=int(np.isfinite(point_map.points[..., 2]).sum()),
        )
        return point_map


__all__ = [
    "DEFAULT_GRID",
    "GeometryEstimator",
    "PointMap",
    "SamGeometryWorker",
    "WorkerGeometry",
    "grid_mask",
    "parse_point_map",
]
