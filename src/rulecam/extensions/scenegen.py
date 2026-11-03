"""Seeded scenes for testing a model-written check (Muse Code extensions, add-on 6).

`generate` returns deterministic samples for a `CheckSpec` (the concepts, named lines and zones
and the frame a check is written for). Every sample has exactly the keys of
`extensions.schema.CheckSample.as_json`, so it can go straight to the sandbox. Two kinds:

- **boundary scenes**, always included: an object's top or bottom edge (its left or right edge
  for an upright line) 1, 3 and 8 px either side of each line at three points along it, an object
  straddling each line, a foot point just inside and just outside each zone edge, no object, two
  and many objects, a line or zone missing, and odd but valid values (a zero-area mask, a box
  touching each frame edge, a low or unknown score, no calibration);
- **random scenes**, `n_random` of them, from a seeded NumPy generator.

`shrink` reduces a failing sample to a small one. It drops objects, zones, lines and the
calibration, rounds coordinates to whole pixels and moves boxes halfway towards the nearest
line, keeping each step only while the failure still shows. Candidates are judged in batches,
because each judgement runs the check in a sandboxed child process.

The geometry helpers (`line_axis`, `line_coord_at`, `side_of_line`, `point_in_polygon`) are
shared with `metamorphic` and `differential`. Generated ground points follow a simple model, the
box's bottom centre divided by `px_per_m`, so scaling pixels and `px_per_m` together keeps them
fixed, and moving a box moves its ground point by the same pixels over `px_per_m`.

Privacy (AGENTS.md rule 7): scenes hold objects by concept only; no track ids, no identities.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, TypedDict

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

Point = tuple[float, float]
Box = tuple[float, float, float, float]
Axis = Literal["horizontal", "vertical"]
Side = Literal["above", "below", "left", "right", "straddles"]

OFFSETS_PX: Final = (1.0, 3.0, 8.0)
ALONG: Final = (0.25, 0.5, 0.75)
BOX_W: Final = 80.0
BOX_H: Final = 60.0
MANY_COLUMNS: Final = 4
MANY_ROWS: Final = 2
MASK_FILL: Final = 0.6
SCORE: Final = 0.9
LOW_SCORE: Final = 0.1
OVERLAP_SHIFT_PX: Final = 10.0
CONCEPT_SHARE: Final = 0.75
MAX_RANDOM_OBJECTS: Final = 5
MIN_RANDOM_SIDE_PX: Final = 20.0
MIN_POLYGON_POINTS: Final = 3
MIN_MOVE_PX: Final = 2.0
START: Final = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class ObjectJSON(TypedDict):
    """One object of a sample, as `extensions.schema.CheckObject` serialises it."""

    concept: str
    box_xyxy_px: list[float]
    mask_top_y_px: float
    mask_area_px: float
    ground_xy_m: list[float] | None
    score: float | None


class SampleJSON(TypedDict):
    """One sample, exactly the dict `extensions.schema.CheckSample.as_json` gives a check."""

    timestamp: str
    frame_w: int | None
    frame_h: int | None
    objects: list[ObjectJSON]
    zones: dict[str, list[list[float]]]
    lines: dict[str, list[list[float]]]
    px_per_m: float | None


FailsBatch = Callable[[list[SampleJSON]], list[bool]]


class CheckSpec(BaseModel):
    """What a check is written for: the scenes worth generating.

    Attributes:
        concepts: The concepts the check looks at, e.g. ("pallet",).
        lines: Owner-drawn reference lines by name, in pixels.
        zones: Polygon zones by name, in pixels.
        frame_w: Frame width in pixels.
        frame_h: Frame height in pixels.
        px_per_m: Pixels per ground metre, or None for an uncalibrated camera.
        unrelated_concepts: Concepts the check must ignore (used by the metamorphic tests).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    concepts: tuple[str, ...] = Field(min_length=1)
    lines: dict[str, tuple[Point, Point]] = Field(default_factory=dict)
    zones: dict[str, tuple[Point, ...]] = Field(default_factory=dict)
    frame_w: int = Field(default=1280, gt=0)
    frame_h: int = Field(default=720, gt=0)
    px_per_m: float | None = Field(default=None, gt=0)
    unrelated_concepts: tuple[str, ...] = ("traffic cone",)

    @model_validator(mode="after")
    def _consistent(self) -> CheckSpec:
        overlap = sorted(set(self.concepts) & set(self.unrelated_concepts))
        if overlap:
            msg = f"concepts cannot also be unrelated: {overlap}"
            raise ValueError(msg)
        for name, polygon in self.zones.items():
            if len(polygon) < MIN_POLYGON_POINTS:
                msg = f"zone {name!r} needs at least {MIN_POLYGON_POINTS} points"
                raise ValueError(msg)
        for name, (start, end) in self.lines.items():
            if start == end:
                msg = f"line {name!r} has zero length"
                raise ValueError(msg)
        return self


# --- geometry -------------------------------------------------------------------------------


def line_axis(line: Sequence[Sequence[float]]) -> Axis:
    """Whether a line is closer to horizontal or to upright.

    Args:
        line: Two points `[[x1, y1], [x2, y2]]` in pixels.

    Returns:
        "horizontal" when it spans at least as many columns as rows, else "vertical".
    """
    (x1, y1), (x2, y2) = line[0], line[1]
    return "horizontal" if abs(x2 - x1) >= abs(y2 - y1) else "vertical"


def line_coord_at(line: Sequence[Sequence[float]], at: float) -> float:
    """The line's y at column `at` (horizontal line) or its x at row `at` (upright line).

    The line is extended past its ends, so every column or row has a value.

    Args:
        line: Two points in pixels.
        at: The column (horizontal line) or row (upright line).

    Returns:
        The line's coordinate there, in pixels.
    """
    (x1, y1), (x2, y2) = line[0], line[1]
    if line_axis(line) == "horizontal":
        return y1 if x2 == x1 else y1 + (at - x1) * (y2 - y1) / (x2 - x1)
    return x1 + (at - y1) * (x2 - x1) / (y2 - y1)


def side_of_line(box: Sequence[float], line: Sequence[Sequence[float]]) -> Side:
    """Where a box is relative to a line, measured at the box's centre.

    Args:
        box: `[x1, y1, x2, y2]` in pixels (y grows downwards).
        line: Two points in pixels.

    Returns:
        "above"/"below" (horizontal line) or "left"/"right" (upright line) when the whole box is
        on one side, else "straddles".
    """
    x1, y1, x2, y2 = box
    if line_axis(line) == "horizontal":
        ref = line_coord_at(line, (x1 + x2) / 2)
        if y2 <= ref:
            return "above"
        return "below" if y1 >= ref else "straddles"
    ref = line_coord_at(line, (y1 + y2) / 2)
    if x2 <= ref:
        return "left"
    return "right" if x1 >= ref else "straddles"


def point_in_polygon(point: Point, polygon: Sequence[Sequence[float]]) -> bool:
    """Whether a point is inside a polygon (ray casting; points on an edge may go either way).

    Args:
        point: `(x, y)` in pixels.
        polygon: The polygon's vertices in pixels.

    Returns:
        True when the point is inside.
    """
    x, y = point
    inside = False
    for index, (xa, ya) in enumerate(polygon):
        xb, yb = polygon[(index + 1) % len(polygon)]
        if (ya > y) != (yb > y) and x < xa + (y - ya) * (xb - xa) / (yb - ya):
            inside = not inside
    return inside


def foot_point(box: Sequence[float]) -> Point:
    """A box's bottom centre, where an object stands on the floor.

    Args:
        box: `[x1, y1, x2, y2]` in pixels.

    Returns:
        `(x, y)` in pixels.
    """
    x1, _, x2, y2 = box
    return ((x1 + x2) / 2, y2)


# --- building samples -----------------------------------------------------------------------


def make_object(
    concept: str,
    box: Box,
    *,
    px_per_m: float | None,
    mask_fill: float = MASK_FILL,
    score: float | None = SCORE,
) -> ObjectJSON:
    """One object with a consistent mask top, mask area and ground point.

    Args:
        concept: The object's concept.
        box: `(x1, y1, x2, y2)` in pixels.
        px_per_m: The camera's scale; None leaves the ground point empty.
        mask_fill: The mask's share of the box area (0 gives a zero-area mask).
        score: The detection score, or None when unknown.

    Returns:
        The object as the check sees it.
    """
    x1, y1, x2, y2 = box
    fx, fy = foot_point(box)
    return ObjectJSON(
        concept=concept,
        box_xyxy_px=[x1, y1, x2, y2],
        mask_top_y_px=y1,
        mask_area_px=(x2 - x1) * (y2 - y1) * mask_fill,
        ground_xy_m=None if px_per_m is None else [fx / px_per_m, fy / px_per_m],
        score=score,
    )


def make_sample(
    spec: CheckSpec,
    objects: Sequence[ObjectJSON],
    *,
    calibrated: bool = True,
    drop_line: str | None = None,
    drop_zone: str | None = None,
) -> SampleJSON:
    """A sample of the spec's frame, lines and zones holding `objects`.

    Args:
        spec: The check spec.
        objects: The objects in view (built with the same `px_per_m`).
        calibrated: False gives the sample no `px_per_m`.
        drop_line: A line to leave out (a "missing line" scene).
        drop_zone: A zone to leave out.

    Returns:
        The sample, stamped at `START` (`generate` re-stamps samples in order).
    """
    return SampleJSON(
        timestamp=START.isoformat(),
        frame_w=spec.frame_w,
        frame_h=spec.frame_h,
        objects=list(objects),
        zones={
            name: [[x, y] for x, y in polygon]
            for name, polygon in spec.zones.items()
            if name != drop_zone
        },
        lines={
            name: [[x, y] for x, y in line]
            for name, line in spec.lines.items()
            if name != drop_line
        },
        px_per_m=spec.px_per_m if calibrated else None,
    )


def copy_object(obj: ObjectJSON) -> ObjectJSON:
    """A deep copy of an object.

    Args:
        obj: The object.

    Returns:
        A copy sharing no lists with `obj`.
    """
    ground = obj["ground_xy_m"]
    return ObjectJSON(
        concept=obj["concept"],
        box_xyxy_px=list(obj["box_xyxy_px"]),
        mask_top_y_px=obj["mask_top_y_px"],
        mask_area_px=obj["mask_area_px"],
        ground_xy_m=None if ground is None else list(ground),
        score=obj["score"],
    )


def copy_sample(sample: SampleJSON) -> SampleJSON:
    """A deep copy of a sample.

    Args:
        sample: The sample.

    Returns:
        A copy sharing no lists or dicts with `sample`.
    """
    return SampleJSON(
        timestamp=sample["timestamp"],
        frame_w=sample["frame_w"],
        frame_h=sample["frame_h"],
        objects=[copy_object(obj) for obj in sample["objects"]],
        zones={name: [list(p) for p in polygon] for name, polygon in sample["zones"].items()},
        lines={name: [list(p) for p in line] for name, line in sample["lines"].items()},
        px_per_m=sample["px_per_m"],
    )


def with_box(obj: ObjectJSON, box: Sequence[float], px_per_m: float | None) -> ObjectJSON:
    """A copy of an object moved or resized to `box`, its mask top and ground point following.

    Args:
        obj: The object.
        box: The new `[x1, y1, x2, y2]` in pixels.
        px_per_m: The sample's scale, to move the ground point.

    Returns:
        The new object.
    """
    out = copy_object(obj)
    old_fx, old_fy = foot_point(obj["box_xyxy_px"])
    new_fx, new_fy = foot_point(box)
    out["box_xyxy_px"] = [float(v) for v in box]
    out["mask_top_y_px"] = obj["mask_top_y_px"] + (box[1] - obj["box_xyxy_px"][1])
    ground = obj["ground_xy_m"]
    if ground is not None and px_per_m is not None:
        out["ground_xy_m"] = [
            ground[0] + (new_fx - old_fx) / px_per_m,
            ground[1] + (new_fy - old_fy) / px_per_m,
        ]
    return out


def shifted_object(obj: ObjectJSON, dx: float, dy: float, px_per_m: float | None) -> ObjectJSON:
    """A copy of an object moved by `(dx, dy)` pixels.

    Args:
        obj: The object.
        dx: Pixels to the right.
        dy: Pixels down.
        px_per_m: The sample's scale, to move the ground point.

    Returns:
        The moved object.
    """
    x1, y1, x2, y2 = obj["box_xyxy_px"]
    return with_box(obj, [x1 + dx, y1 + dy, x2 + dx, y2 + dy], px_per_m)


def shift_points(points: Sequence[Sequence[float]], dx: float, dy: float) -> list[list[float]]:
    """Points (a zone or a line) moved by `(dx, dy)` pixels.

    Args:
        points: `[[x, y], ...]` in pixels.
        dx: Pixels to the right.
        dy: Pixels down.

    Returns:
        New points.
    """
    return [[x + dx, y + dy] for x, y in points]


def inside_frame(sample: SampleJSON) -> bool:
    """Whether every box, zone point and line point lies inside the frame (when it is known).

    Args:
        sample: The sample.

    Returns:
        True when the scene fits its frame, or the frame size is unknown.
    """
    width, height = sample["frame_w"], sample["frame_h"]
    if width is None or height is None:
        return True
    for x1, y1, x2, y2 in (obj["box_xyxy_px"] for obj in sample["objects"]):
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            return False
    shapes = [*sample["zones"].values(), *sample["lines"].values()]
    return all(0 <= x <= width and 0 <= y <= height for shape in shapes for x, y in shape)


# --- boundary scenes ------------------------------------------------------------------------


def _signed(offsets: Sequence[float]) -> Iterator[float]:
    for offset in offsets:
        yield offset
        yield -offset


def _boxes_near_line(line: tuple[Point, Point], along: float) -> list[Box]:
    """Boxes whose near or far edge sits each offset either side of the line, plus a straddler.

    A positive offset puts the edge above the line (left of it for an upright line).
    """
    (x1, y1), (x2, y2) = line
    px, py = x1 + along * (x2 - x1), y1 + along * (y2 - y1)
    half_w, half_h = BOX_W / 2, BOX_H / 2
    boxes: list[Box] = []
    if line_axis(line) == "horizontal":
        for d in _signed(OFFSETS_PX):
            boxes.append((px - half_w, py - d, px + half_w, py - d + BOX_H))
            boxes.append((px - half_w, py - d - BOX_H, px + half_w, py - d))
        boxes.append((px - half_w, py - half_h, px + half_w, py + half_h))
        return boxes
    for d in _signed(OFFSETS_PX):
        boxes.append((px - d, py - half_h, px - d + BOX_W, py + half_h))
        boxes.append((px - d - BOX_W, py - half_h, px - d, py + half_h))
    boxes.append((px - half_w, py - half_h, px + half_w, py + half_h))
    return boxes


def _box_standing_at(point: Point) -> Box:
    x, y = point
    return (x - BOX_W / 2, y - BOX_H, x + BOX_W / 2, y)


def _fits(box: Box, spec: CheckSpec) -> bool:
    x1, y1, x2, y2 = box
    return 0 <= x1 < x2 <= spec.frame_w and 0 <= y1 < y2 <= spec.frame_h


def _single(spec: CheckSpec, concept: str, boxes: Sequence[Box]) -> list[SampleJSON]:
    """One sample per box that fits the frame, each holding one object of `concept`."""
    return [
        make_sample(spec, [make_object(concept, box, px_per_m=spec.px_per_m)])
        for box in boxes
        if _fits(box, spec)
    ]


def _line_scenes(spec: CheckSpec, concept: str) -> list[SampleJSON]:
    boxes = [
        box
        for line in spec.lines.values()
        for along in ALONG
        for box in _boxes_near_line(line, along)
    ]
    return _single(spec, concept, boxes)


def _inward_normal(start: Point, end: Point, centroid: Point) -> Point:
    """The unit normal of an edge that points towards the polygon's centroid."""
    ex, ey = end[0] - start[0], end[1] - start[1]
    length = (ex * ex + ey * ey) ** 0.5
    nx, ny = -ey / length, ex / length
    mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
    if (centroid[0] - mx) * nx + (centroid[1] - my) * ny < 0:
        return (-nx, -ny)
    return (nx, ny)


def _centroid(polygon: Sequence[Point]) -> Point:
    return (
        sum(x for x, _ in polygon) / len(polygon),
        sum(y for _, y in polygon) / len(polygon),
    )


def _zone_feet(polygon: Sequence[Point]) -> list[Point]:
    """Foot points each offset inside and outside every edge's midpoint, plus the centroid."""
    centroid = _centroid(polygon)
    feet = [centroid]
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if start == end:
            continue
        nx, ny = _inward_normal(start, end, centroid)
        mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
        feet.extend((mx + d * nx, my + d * ny) for d in _signed(OFFSETS_PX))
    return feet


def _zone_scenes(spec: CheckSpec, concept: str) -> list[SampleJSON]:
    boxes = [
        _box_standing_at(foot) for polygon in spec.zones.values() for foot in _zone_feet(polygon)
    ]
    return _single(spec, concept, boxes)


def _grid_boxes(spec: CheckSpec, columns: int, rows: int) -> list[Box]:
    centres = [
        ((col + 0.5) * spec.frame_w / columns, (row + 0.5) * spec.frame_h / rows)
        for row in range(rows)
        for col in range(columns)
    ]
    boxes = [(x - BOX_W / 2, y - BOX_H / 2, x + BOX_W / 2, y + BOX_H / 2) for x, y in centres]
    return [box for box in boxes if _fits(box, spec)]


def _count_scenes(spec: CheckSpec, concept: str) -> list[SampleJSON]:
    """Two objects side by side, and many objects on a grid."""
    scenes = []
    for columns, rows in ((2, 1), (MANY_COLUMNS, MANY_ROWS)):
        objects = [
            make_object(concept, box, px_per_m=spec.px_per_m)
            for box in _grid_boxes(spec, columns, rows)
        ]
        scenes.append(make_sample(spec, objects))
    return scenes


def _centre_box(spec: CheckSpec) -> Box:
    cx, cy = spec.frame_w / 2, spec.frame_h / 2
    return (cx - BOX_W / 2, cy - BOX_H / 2, cx + BOX_W / 2, cy + BOX_H / 2)


def _edge_boxes(spec: CheckSpec) -> list[Box]:
    """Boxes touching the left, top, right and bottom frame edges."""
    w, h = float(spec.frame_w), float(spec.frame_h)
    cx, cy = w / 2, h / 2
    return [
        (0.0, cy - BOX_H / 2, BOX_W, cy + BOX_H / 2),
        (cx - BOX_W / 2, 0.0, cx + BOX_W / 2, BOX_H),
        (w - BOX_W, cy - BOX_H / 2, w, cy + BOX_H / 2),
        (cx - BOX_W / 2, h - BOX_H, cx + BOX_W / 2, h),
    ]


def _odd_scenes(spec: CheckSpec, concept: str) -> list[SampleJSON]:
    """Odd but valid values: zero-area mask, frame-edge boxes, low/unknown score, no scale."""
    ppm = spec.px_per_m
    centre = _centre_box(spec)
    odd = [
        make_object(concept, centre, px_per_m=ppm, mask_fill=0.0),
        make_object(concept, centre, px_per_m=ppm, score=LOW_SCORE),
        make_object(concept, centre, px_per_m=ppm, score=None),
    ]
    scenes = [make_sample(spec, [obj]) for obj in odd if _fits(centre, spec)]
    scenes.extend(_single(spec, concept, _edge_boxes(spec)))
    if spec.unrelated_concepts and _fits(centre, spec):
        x1, y1, x2, y2 = centre
        s = OVERLAP_SHIFT_PX
        neighbour = make_object(
            spec.unrelated_concepts[0], (x1 + s, y1 + s, x2 + s, y2 + s), px_per_m=ppm
        )
        scenes.append(make_sample(spec, [make_object(concept, centre, px_per_m=ppm), neighbour]))
    if ppm is not None:
        straddlers = [_boxes_near_line(line, 0.5)[-1] for line in spec.lines.values()] or [centre]
        scenes.extend(
            make_sample(spec, [make_object(concept, box, px_per_m=None)], calibrated=False)
            for box in straddlers
            if _fits(box, spec)
        )
    return scenes


def _missing_scenes(spec: CheckSpec) -> list[SampleJSON]:
    """A straddler with its line left out, and a centroid object with its zone left out."""
    concept, ppm = spec.concepts[0], spec.px_per_m
    scenes = []
    for name, line in spec.lines.items():
        box = _boxes_near_line(line, 0.5)[-1]
        if _fits(box, spec):
            obj = make_object(concept, box, px_per_m=ppm)
            scenes.append(make_sample(spec, [obj], drop_line=name))
    for name, polygon in spec.zones.items():
        box = _box_standing_at(_centroid(polygon))
        if _fits(box, spec):
            obj = make_object(concept, box, px_per_m=ppm)
            scenes.append(make_sample(spec, [obj], drop_zone=name))
    return scenes


def boundary_scenes(spec: CheckSpec) -> list[SampleJSON]:
    """Every boundary scene for a spec, in a fixed order.

    Args:
        spec: The check spec.

    Returns:
        The scenes (unstamped; `generate` stamps them).
    """
    scenes = [make_sample(spec, [])]
    for concept in spec.concepts:
        scenes.extend(_line_scenes(spec, concept))
        scenes.extend(_zone_scenes(spec, concept))
        scenes.extend(_count_scenes(spec, concept))
        scenes.extend(_odd_scenes(spec, concept))
    scenes.extend(_missing_scenes(spec))
    return scenes


# --- random scenes --------------------------------------------------------------------------


def _random_object(spec: CheckSpec, rng: np.random.Generator) -> ObjectJSON:
    pool = spec.concepts
    if spec.unrelated_concepts and rng.random() >= CONCEPT_SHARE:
        pool = spec.unrelated_concepts
    concept = pool[int(rng.integers(0, len(pool)))]
    w = float(rng.uniform(MIN_RANDOM_SIDE_PX, max(MIN_RANDOM_SIDE_PX, spec.frame_w / 3)))
    h = float(rng.uniform(MIN_RANDOM_SIDE_PX, max(MIN_RANDOM_SIDE_PX, spec.frame_h / 3)))
    x1 = round(float(rng.uniform(0, max(0.0, spec.frame_w - w))), 1)
    y1 = round(float(rng.uniform(0, max(0.0, spec.frame_h - h))), 1)
    box = (
        x1,
        y1,
        min(round(x1 + w, 1), float(spec.frame_w)),
        min(round(y1 + h, 1), float(spec.frame_h)),
    )
    return make_object(
        concept,
        box,
        px_per_m=spec.px_per_m,
        mask_fill=round(float(rng.uniform(0.2, 1.0)), 2),
        score=round(float(rng.uniform(0.3, 1.0)), 2),
    )


def random_scenes(spec: CheckSpec, n: int, rng: np.random.Generator) -> list[SampleJSON]:
    """`n` random scenes of up to `MAX_RANDOM_OBJECTS` objects.

    Args:
        spec: The check spec.
        n: How many scenes.
        rng: The seeded generator.

    Returns:
        The scenes (unstamped).
    """
    scenes = []
    for _ in range(n):
        count = int(rng.integers(0, MAX_RANDOM_OBJECTS + 1))
        scenes.append(make_sample(spec, [_random_object(spec, rng) for _ in range(count)]))
    return scenes


def generate(spec: CheckSpec, *, n_random: int = 200, seed: int = 0) -> list[SampleJSON]:
    """All boundary scenes, then `n_random` seeded random scenes, stamped one second apart.

    Args:
        spec: The check spec.
        n_random: How many random scenes to add (0 gives boundary scenes only).
        seed: The random seed; the same seed always gives the same samples.

    Returns:
        The samples.

    Raises:
        ValueError: If `n_random` is negative.
    """
    if n_random < 0:
        msg = f"n_random must be at least 0, got {n_random}"
        raise ValueError(msg)
    scenes = boundary_scenes(spec) + random_scenes(spec, n_random, np.random.default_rng(seed))
    for index, scene in enumerate(scenes):
        scene["timestamp"] = (START + timedelta(seconds=index)).isoformat()
    return scenes


# --- shrinking ------------------------------------------------------------------------------


def _drops(sample: SampleJSON) -> list[SampleJSON]:
    """Candidates with one object, zone or line fewer, or with no calibration."""
    out: list[SampleJSON] = []
    for index in range(len(sample["objects"])):
        candidate = copy_sample(sample)
        del candidate["objects"][index]
        out.append(candidate)
    for name in sample["zones"]:
        candidate = copy_sample(sample)
        del candidate["zones"][name]
        out.append(candidate)
    for name in sample["lines"]:
        candidate = copy_sample(sample)
        del candidate["lines"][name]
        out.append(candidate)
    if sample["px_per_m"] is not None:
        candidate = copy_sample(sample)
        candidate["px_per_m"] = None
        for obj in candidate["objects"]:
            obj["ground_xy_m"] = None
        out.append(candidate)
    return out


def _is_whole(obj: ObjectJSON) -> bool:
    values = [*obj["box_xyxy_px"], obj["mask_top_y_px"], obj["mask_area_px"]]
    return all(float(v).is_integer() for v in values)


def _rounded(sample: SampleJSON) -> list[SampleJSON]:
    """One candidate with every object's box, mask top and mask area in whole pixels."""
    if all(_is_whole(obj) for obj in sample["objects"]):
        return []
    candidate = copy_sample(sample)
    objects = []
    for obj in candidate["objects"]:
        moved = with_box(obj, [float(round(v)) for v in obj["box_xyxy_px"]], sample["px_per_m"])
        moved["mask_top_y_px"] = float(round(moved["mask_top_y_px"]))
        moved["mask_area_px"] = float(round(moved["mask_area_px"]))
        objects.append(moved)
    candidate["objects"] = objects
    return [candidate]


def _halfway(box: Sequence[float], line: Sequence[Sequence[float]]) -> Point | None:
    """The shift that halves the gap between the box's nearer edge and the line.

    None when that gap is already under `MIN_MOVE_PX`.
    """
    x1, y1, x2, y2 = box
    if line_axis(line) == "horizontal":
        ref = line_coord_at(line, (x1 + x2) / 2)
        gap = min(y1 - ref, y2 - ref, key=abs)
        return None if abs(gap) < MIN_MOVE_PX else (0.0, round(gap / 2) - gap)
    ref = line_coord_at(line, (y1 + y2) / 2)
    gap = min(x1 - ref, x2 - ref, key=abs)
    return None if abs(gap) < MIN_MOVE_PX else (round(gap / 2) - gap, 0.0)


def _moves(sample: SampleJSON) -> list[SampleJSON]:
    """Candidates with one object moved halfway towards one line (staying in the frame)."""
    out: list[SampleJSON] = []
    for index, obj in enumerate(sample["objects"]):
        for line in sample["lines"].values():
            shift = _halfway(obj["box_xyxy_px"], line)
            if shift is None:
                continue
            candidate = copy_sample(sample)
            candidate["objects"][index] = shifted_object(obj, *shift, sample["px_per_m"])
            if inside_frame(candidate):
                out.append(candidate)
    return out


def simpler(sample: SampleJSON) -> list[SampleJSON]:
    """Every one-step simplification of a sample, simplest kind first.

    Args:
        sample: The sample.

    Returns:
        Drops first, then rounding, then moves towards a line.
    """
    return [*_drops(sample), *_rounded(sample), *_moves(sample)]


def shrink(sample: SampleJSON, fails: FailsBatch, *, max_rounds: int = 40) -> SampleJSON:
    """Reduce a failing sample to a small one that still fails.

    Each round judges every one-step simplification (`simpler`) in one call to `fails` and keeps
    the first that still fails; it stops when none does or after `max_rounds`.

    Args:
        sample: A sample on which the failure shows.
        fails: Judges a batch of candidates; True where the failure still shows.
        max_rounds: Upper bound on rounds.

    Returns:
        The smallest failing sample found (`sample` itself when nothing simpler fails).
    """
    current = copy_sample(sample)
    for _ in range(max_rounds):
        candidates = simpler(current)
        if not candidates:
            break
        verdicts = fails(candidates)
        found = next(
            (c for c, bad in zip(candidates, verdicts, strict=True) if bad),
            None,
        )
        if found is None:
            break
        current = found
    return current
