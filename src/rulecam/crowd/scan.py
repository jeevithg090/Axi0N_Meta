"""One crowd reading of a camera: SAM 3.1 on tiles, density per square metre, motion, pressure.

`CrowdScanner.scan` takes a recorded frame, the frame `gap_s` before it, the camera's ground
calibration (if any) and the zones to report on, and returns a `CrowdReading`:

- the whole view (`view`) and each named zone: people, ground area, density, mean speed, speed
  spread, velocity variance, direction coherence, counterflow share and crowd pressure;
- a coarse grid of cells with each cell's people, area and density, and the densest one;
- a heatmap image (density per cell over the frame, with each cell's mean motion as an arrow)
  that Glimmer and the owner can look at.

The concept SAM counts is chosen by the caller (a model, or the site's setting); nothing here
decides what a reading means.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

import cv2
import numpy as np
from cv2.typing import MatLike
from pydantic import Field

from rulecam.ask.vision import Frame
from rulecam.contracts import Calibration, Frozen
from rulecam.crowd import signals
from rulecam.crowd.zones import CrowdZone
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter

_log = get_logger(__name__)

DEFAULT_CONCEPT: Final = "person"
DEFAULT_GRID: Final = 2  # 2 x 2 tiles: SAM sees each person about twice as large
DEFAULT_CELLS: Final = (8, 6)  # density grid columns x rows
VIEW: Final = "view"
_HEAT_MAX_PPM2: Final = 6.0  # colour scale top of the heatmap (display only)
_TILE_WORKERS: Final = 4


class ZoneReading(Frozen):
    """The crowd in one area at one instant (`None` where it cannot be measured)."""

    zone: str
    people: int
    area_m2: float | None = None
    density_ppm2: float | None = None
    moving: int = 0
    speed_mps: float | None = None
    speed_sd_mps: float | None = None
    velocity_var: float | None = None
    coherence: float | None = None
    counterflow_share: float | None = None
    pressure: float | None = None


class CellReading(Frozen):
    """One cell of the density grid."""

    row: int
    col: int
    people: int
    area_m2: float
    density_ppm2: float | None


class CrowdReading(Frozen):
    """A camera's crowd at one instant."""

    camera_id: str
    at: datetime
    concept: str
    grid: int
    people: int
    calibration: str
    calibration_err_m: float | None = None
    zones: list[ZoneReading]
    cells: list[CellReading] = Field(default_factory=list)
    peak_density_ppm2: float | None = None
    peak_cell: tuple[int, int] | None = None
    heatmap: str | None = None
    frame: str | None = None
    seconds: float = 0.0
    notes: list[str] = Field(default_factory=list)

    def zone(self, name: str) -> ZoneReading | None:
        """The reading of one zone, by name."""
        return next((z for z in self.zones if z.zone == name), None)


class CrowdScanner:
    """Measures the crowd on recorded frames with SAM 3.1 on tiles and optical flow."""

    def __init__(
        self,
        *,
        segmenter: ImageSegmenter,
        out_dir: Path,
        concept: str = DEFAULT_CONCEPT,
        grid: int = DEFAULT_GRID,
        cells: tuple[int, int] = DEFAULT_CELLS,
    ) -> None:
        """Create the scanner.

        Args:
            segmenter: SAM 3.1 on single images.
            out_dir: Where tiles and heatmaps are written.
            concept: What SAM counts unless the caller says otherwise.
            grid: Tiles per side unless the caller says otherwise.
            cells: Columns and rows of the density grid.
        """
        self._segmenter = segmenter
        self._out_dir = out_dir
        self._concept = concept
        self._grid = grid
        self._cells = cells

    @property
    def out_dir(self) -> Path:
        """Where heatmaps are written."""
        return self._out_dir

    @property
    def concept(self) -> str:
        """What SAM counts unless the caller says otherwise."""
        return self._concept

    @property
    def grid(self) -> int:
        """Tiles per side unless the caller says otherwise."""
        return self._grid

    def detect(self, frame: Frame, concept: str, grid: int) -> list[signals.Box]:
        """SAM 3.1's boxes of `concept` on the frame's tiles, merged into frame pixels.

        Raises:
            OSError: The frame cannot be read.
        """
        image = cv2.imread(str(frame.path))
        if image is None:
            msg = f"cannot read frame {frame.path.name}"
            raise OSError(msg)
        height, width = image.shape[:2]
        tiles = signals.tile_boxes(width, height, grid)
        if len(tiles) == 1:
            return signals.merge_boxes(self._segment(frame.path, concept, frame.camera_id, 0, 0))
        tile_dir = self._out_dir / "tiles"
        tile_dir.mkdir(parents=True, exist_ok=True)
        stem = hashlib.sha256(f"{frame.path}|{grid}".encode()).hexdigest()[:16]
        paths = []
        for k, (x1, y1, x2, y2) in enumerate(tiles):
            path = tile_dir / f"{stem}-{k}.jpg"
            cv2.imwrite(str(path), image[y1:y2, x1:x2])
            paths.append(path)
        with ThreadPoolExecutor(min(_TILE_WORKERS, len(tiles)), thread_name_prefix="crowd") as pool:
            found = pool.map(
                lambda job: self._segment(job[0], concept, frame.camera_id, job[1][0], job[1][1]),
                zip(paths, tiles, strict=True),
            )
            boxes = [box for tile in found for box in tile]
        for path in paths:
            path.unlink(missing_ok=True)
        return signals.merge_boxes(boxes)

    def _segment(
        self, path: Path, concept: str, camera_id: str, dx: int, dy: int
    ) -> list[signals.Box]:
        found = self._segmenter.segment_image(path, concept, camera_id=camera_id)
        return [
            (
                d.mask.box_xyxy[0] + dx,
                d.mask.box_xyxy[1] + dy,
                d.mask.box_xyxy[2] + dx,
                d.mask.box_xyxy[3] + dy,
                d.mask.confidence if d.mask.confidence is not None else 1.0,
            )
            for d in found
        ]

    def scan(
        self,
        frame: Frame,
        before: Frame | None,
        calibration: tuple[Calibration, str] | None,
        zones: Sequence[CrowdZone] = (),
        *,
        concept: str | None = None,
        grid: int | None = None,
        boxes: Sequence[signals.Box] | None = None,
    ) -> CrowdReading:
        """Measure the crowd at `frame` (with motion from `before`, when given).

        Args:
            frame: The frame to measure.
            before: A frame shortly before it, for motion; `None` skips motion.
            calibration: The camera's ground calibration and where it came from; `None` leaves
                areas, densities and speeds in m/s unmeasured.
            zones: Named areas to report besides the whole view.
            concept: What SAM counts (default: the scanner's).
            grid: Tiles per side (default: the scanner's).
            boxes: Detections already made on `frame` (skips SAM).

        Returns:
            The reading.

        Raises:
            OSError: The frame cannot be read.
            RulecamError: SAM fails (`rulecam.errors`).
        """
        started = time.perf_counter()
        concept = concept or self._concept
        grid = grid or self._grid
        image = cv2.imread(str(frame.path))
        if image is None:
            msg = f"cannot read frame {frame.path.name}"
            raise OSError(msg)
        height, width = image.shape[:2]
        found = list(boxes) if boxes is not None else self.detect(frame, concept, grid)
        feet = signals.foot_points(found)
        notes: list[str] = []
        h = signals.homography(calibration[0]) if calibration is not None else None
        ground = _Ground.of(h, width, height) if h is not None else None
        if h is None:
            notes.append("no ground calibration: areas, densities and m/s speeds are unknown")
        velocities = self._velocities(
            image, before, found=found, feet=feet, h=h, at=frame.at, notes=notes
        )
        view = [(0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, height)]
        readings = [
            _zone(VIEW, view, feet=feet, velocities=velocities, ground=ground),
            *(
                _zone(z.name, z.polygon_px, feet=feet, velocities=velocities, ground=ground)
                for z in zones
            ),
        ]
        cells = _cells(feet, ground, self._cells) if ground is not None else []
        dense = [c for c in cells if c.density_ppm2 is not None]
        peak = max(dense, key=lambda c: c.density_ppm2 or 0.0) if dense else None
        heat = _heatmap(
            image, cells, feet=feet, velocities=velocities, shape=self._cells, view=readings[0]
        )
        name = f"crowd-{frame.camera_id}-{frame.at.strftime('%Y%m%dT%H%M%S%f')}.jpg"
        self._out_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(self._out_dir / name), heat)
        return CrowdReading(
            camera_id=frame.camera_id,
            at=frame.at,
            concept=concept,
            grid=grid,
            people=len(found),
            calibration=calibration[1] if calibration is not None else "none",
            calibration_err_m=calibration[0].err_m if calibration is not None else None,
            zones=readings,
            cells=cells,
            peak_density_ppm2=_round(peak.density_ppm2) if peak is not None else None,
            peak_cell=(peak.row, peak.col) if peak is not None else None,
            heatmap=name,
            frame=frame.path.name,
            seconds=round(time.perf_counter() - started, 2),
            notes=notes,
        )

    def _velocities(
        self,
        image: MatLike,
        before: Frame | None,
        *,
        found: Sequence[signals.Box],
        feet: signals.FloatArray,
        h: signals.FloatArray | None,
        at: datetime,
        notes: list[str],
    ) -> signals.FloatArray:
        """Each detection's velocity (m/s, or px/s without a calibration); NaN when unknown."""
        unknown = np.full((len(found), 2), np.nan)
        if before is None or not found:
            return unknown
        earlier = cv2.imread(str(before.path))
        dt = (at - before.at).total_seconds()
        if earlier is None or earlier.shape != image.shape or dt <= 0:
            notes.append("no usable earlier frame: motion is unknown")
            return unknown
        moved = signals.box_flow(signals.optical_flow(earlier, image), found)
        if h is None:
            return moved / dt
        return signals.ground_velocities(h, feet, moved, dt)


@dataclass(frozen=True)
class _Ground:
    """A calibrated frame's geometry: the homography and the area of each pixel block."""

    h: signals.FloatArray
    areas: signals.FloatArray
    width: int
    height: int

    @classmethod
    def of(cls, h: signals.FloatArray, width: int, height: int) -> _Ground:
        return cls(h, signals.area_map(h, width, height), width, height)

    def area(self, polygon: Sequence[tuple[float, float]]) -> float:
        return signals.region_area(self.h, self.areas, polygon, self.width, self.height)


def _zone(
    name: str,
    polygon: Sequence[tuple[float, float]],
    *,
    feet: signals.FloatArray,
    velocities: signals.FloatArray,
    ground: _Ground | None,
) -> ZoneReading:
    chosen = signals.inside(polygon, feet) if len(feet) else np.zeros(0, dtype=bool)
    people = int(chosen.sum())
    area = ground.area(polygon) if ground is not None else None
    moved = signals.motion(velocities[chosen])
    dense = signals.density(people, area)
    return ZoneReading(
        zone=name,
        people=people,
        area_m2=_round(area),
        density_ppm2=_round(dense),
        moving=moved.moving,
        speed_mps=_round(moved.speed),
        speed_sd_mps=_round(moved.speed_sd),
        velocity_var=_round(moved.velocity_var, 4),
        coherence=_round(moved.coherence),
        counterflow_share=_round(moved.counterflow),
        pressure=_round(signals.pressure(dense, moved.velocity_var), 4),
    )


def _cells(feet: signals.FloatArray, ground: _Ground, shape: tuple[int, int]) -> list[CellReading]:
    cols, rows = shape
    out = []
    for k, rect in enumerate(signals.cell_rects(ground.width, ground.height, cols, rows)):
        people = int(signals.in_rect(feet, rect).sum())
        area = ground.area(signals.rect_polygon(rect))
        out.append(
            CellReading(
                row=k // cols,
                col=k % cols,
                people=people,
                area_m2=round(area, 2),
                density_ppm2=_round(signals.density(people, area)),
            )
        )
    return out


def _heatmap(
    image: MatLike,
    cells: Sequence[CellReading],
    *,
    feet: signals.FloatArray,
    velocities: signals.FloatArray,
    shape: tuple[int, int],
    view: ZoneReading,
) -> MatLike:
    """The frame with each cell tinted by its density, its number, and its mean motion arrow."""
    height, width = image.shape[:2]
    cols, rows = shape
    out = image.copy()
    tint = image.copy()
    rects = signals.cell_rects(width, height, cols, rows)
    for cell, rect in zip(cells, rects, strict=False):
        if cell.density_ppm2 is None:
            continue
        level = min(1.0, cell.density_ppm2 / _HEAT_MAX_PPM2)
        colour = cv2.applyColorMap(
            np.array([[round(level * 255)]], dtype=np.uint8), cv2.COLORMAP_JET
        )[0, 0]
        cv2.rectangle(tint, rect[:2], (rect[2] - 1, rect[3] - 1), colour.tolist(), -1)
    cv2.addWeighted(tint, 0.45, out, 0.55, 0, out)
    for cell, rect in zip(cells, rects, strict=False):
        if cell.density_ppm2 is not None and cell.people:
            _label(out, f"{cell.density_ppm2:.1f}/m2", (rect[0] + 4, rect[1] + 16))
    for rect in rects:
        chosen = signals.in_rect(feet, rect)
        v = velocities[chosen] if len(velocities) else velocities
        v = v[np.isfinite(v).all(axis=1)] if len(v) else v
        if not len(v):
            continue
        mean = v.mean(axis=0)
        cx, cy = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
        scale = 25.0 / max(1e-6, float(np.hypot(*mean))) * min(1.0, float(np.hypot(*mean)))
        tip = (round(cx + mean[0] * scale), round(cy + mean[1] * scale))
        cv2.arrowedLine(out, (round(cx), round(cy)), tip, (255, 255, 255), 2, tipLength=0.35)
    for x, y in feet:
        cv2.circle(out, (round(x), round(y)), 3, (0, 255, 255), -1)
    summary = f"{view.people} people"
    if view.density_ppm2 is not None:
        summary += f"  view {view.density_ppm2:.2f}/m2"
    if view.speed_mps is not None:
        summary += f"  {view.speed_mps:.2f} m/s"
    _label(out, summary, (8, height - 10), scale=0.7)
    return out


def _label(image: MatLike, text: str, at: tuple[int, int], scale: float = 0.5) -> None:
    cv2.putText(image, text, at, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, text, at, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


__all__ = [
    "DEFAULT_CONCEPT",
    "DEFAULT_GRID",
    "VIEW",
    "CellReading",
    "CrowdReading",
    "CrowdScanner",
    "CrowdZone",
    "ZoneReading",
]
