"""Scene layout for synthetic Rule CI traces (task P2-08).

Trace geometry lives on a synthetic camera where 100 px is 1 m on the ground, so a rule's pixel
polygons and metric buffers share one coordinate system. `Region` is this package's own model of
where a zone is (shapely for polygons, point-to-segment distance for buffers). It is used to lay
traces out and to state their expected outcomes; it never calls the rules engine under test.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final

from shapely.geometry import LineString, Point, Polygon

from rulecam.contracts.core import Calibration
from rulecam.contracts.rule_ir import ZoneSpec

PX_PER_M: Final = 100.0
ZONE_EDGE_EPS_M: Final = 0.05
OBJECT_BOX_M: Final = (0.4, 0.8)
"""Width and height of a non-person object's box; its ground footprint is the bottom edge."""
ANCHOR_SPACING_M: Final = 3.0

XY = tuple[float, float]


class UnsupportedRuleError(ValueError):
    """Rule CI has no boundary-trace generator for this rule's shape or layout."""


def synthetic_calibration(camera_id: str) -> Calibration:
    """The synthetic camera's ground calibration (100 px = 1 m, no error)."""
    scale = 1 / PX_PER_M
    return Calibration(
        camera_id=camera_id,
        homography=((scale, 0.0, 0.0), (0.0, scale, 0.0), (0.0, 0.0, 1.0)),
        method="clicks4",
        err_m=0.0,
    )


@dataclass(frozen=True)
class Region:
    """A rule zone in ground metres: an image polygon, or a buffer around a static anchor.

    Attributes:
        name: The rule's zone name.
        polygon: The polygon (metres), for polygon zones.
        anchor: Foot point of the buffer's concept, for buffer zones (placed by the layout).
        anchor_concept: The buffer's concept.
        buffer_m: The buffer radius.
    """

    name: str
    polygon: Polygon | None = None
    anchor: XY | None = None
    anchor_concept: str | None = None
    buffer_m: float = 0.0

    def contains(self, point: XY) -> bool:
        """Whether a ground point lies in the zone (polygon interior, or within the buffer)."""
        if self.polygon is not None:
            return bool(self.polygon.contains(Point(point)))
        return bool(Point(point).distance(self._footprint()) <= self.buffer_m)

    def inside(self) -> XY:
        """A point well inside the zone."""
        if self.polygon is not None:
            centre = self.polygon.representative_point()
            return (centre.x, centre.y)
        ax, ay = self._anchor()
        return (ax, ay + self.buffer_m / 2)

    def edge_pairs(self) -> Iterator[tuple[XY, XY]]:
        """Candidate `(just_inside, just_outside)` points, `ZONE_EDGE_EPS_M` off an edge."""
        if self.polygon is None:
            ax, ay = self._anchor()
            yield (
                (ax, ay + self.buffer_m - ZONE_EDGE_EPS_M),
                (ax, ay + self.buffer_m + ZONE_EDGE_EPS_M),
            )
            return
        coords = list(self.polygon.exterior.coords)
        for (x1, y1), (x2, y2) in itertools.pairwise(coords):
            length = math.hypot(x2 - x1, y2 - y1)
            if length == 0:
                continue
            mid = ((x1 + x2) / 2, (y1 + y2) / 2)
            normal = (-(y2 - y1) / length, (x2 - x1) / length)
            a = (mid[0] + normal[0] * ZONE_EDGE_EPS_M, mid[1] + normal[1] * ZONE_EDGE_EPS_M)
            b = (mid[0] - normal[0] * ZONE_EDGE_EPS_M, mid[1] - normal[1] * ZONE_EDGE_EPS_M)
            if self.contains(a) != self.contains(b):
                yield (a, b) if self.contains(a) else (b, a)

    def extent(self) -> tuple[float, float]:
        """The largest x and y the zone reaches."""
        if self.polygon is not None:
            _, _, max_x, max_y = self.polygon.bounds
            return max_x, max_y
        ax, ay = self._anchor()
        return ax + self.buffer_m, ay + self.buffer_m

    def _anchor(self) -> XY:
        if self.anchor is None:
            msg = f"buffer zone {self.name!r} has no anchor"
            raise UnsupportedRuleError(msg)
        return self.anchor

    def _footprint(self) -> LineString:
        ax, ay = self._anchor()
        half = OBJECT_BOX_M[0] / 2
        return LineString([(ax - half, ay), (ax + half, ay)])


def layout_regions(zones: dict[str, ZoneSpec], names: list[str]) -> dict[str, Region]:
    """Turn the named rule zones into regions, placing buffer anchors clear of every polygon.

    Args:
        zones: The rule's zone specs.
        names: The zones to lay out.

    Returns:
        Regions by zone name.

    Raises:
        UnsupportedRuleError: A zone is missing, incomplete, or on another camera.
    """
    if len({zones[n].camera_id for n in names if n in zones}) > 1:
        msg = "zones on more than one camera"
        raise UnsupportedRuleError(msg)
    regions: dict[str, Region] = {}
    for name in names:
        spec = zones.get(name)
        if spec is not None and spec.kind == "polygon" and spec.polygon_px:
            ring = [(x / PX_PER_M, y / PX_PER_M) for x, y in spec.polygon_px]
            regions[name] = Region(name=name, polygon=Polygon(ring))
    reach_x = max((r.extent()[0] for r in regions.values()), default=0.0)
    for name in names:
        spec = zones.get(name)
        if name in regions:
            continue
        if spec is None or spec.kind != "buffer" or not spec.buffer_of or spec.buffer_m is None:
            msg = f"zone {name!r} is neither a polygon nor a complete buffer"
            raise UnsupportedRuleError(msg)
        anchor = (reach_x + ANCHOR_SPACING_M + spec.buffer_m, ANCHOR_SPACING_M)
        regions[name] = Region(
            name=name, anchor=anchor, anchor_concept=spec.buffer_of, buffer_m=spec.buffer_m
        )
        reach_x = anchor[0] + spec.buffer_m
    return regions


def far_point(regions: dict[str, Region]) -> XY:
    """A point outside every region, even after a 50% zone widening."""
    reach = [r.extent() for r in regions.values()]
    return (
        max((x for x, _ in reach), default=0.0) * 2 + 10,
        max((y for _, y in reach), default=0.0) * 2 + 10,
    )


def pick_edge(region: Region, ok: Callable[[XY], bool]) -> tuple[XY, XY]:
    """The first just-inside/just-outside pair of `region` whose points both satisfy `ok`.

    Raises:
        UnsupportedRuleError: No edge pair satisfies `ok`.
    """
    for inside, outside in region.edge_pairs():
        if ok(inside) and ok(outside):
            return inside, outside
    msg = f"no clear edge of zone {region.name!r} for boundary traces"
    raise UnsupportedRuleError(msg)
