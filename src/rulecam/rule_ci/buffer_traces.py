"""Boundary traces for buffer-zone rules (task P2-08, MP §1, §3.4).

Shape: `[persist] inside(subject, Z)` where `Z` is a buffer zone (`buffer_m` metres around the
ground footprint of concept X), alone or under `all` with `moving(X)` or `moving(subject)`. The
compiler writes "keep people 2 m away from a moving forklift" either this way,
`all(inside(person, <2 m around forklift>), moving(forklift))`, or as
`all(distance_m(person, forklift) < 2, moving(forklift))` (`traces.Proximity`); both are valid
Rule IR and Rule CI proves both (no normalisation to one form; see the README).

**Calibration.** A buffer is metric, so the traces are laid out on the rule's ground plane: the
real homography of the zone's camera when Rule CI is given one, else the synthetic plane
(100 px = 1 m) when the rule names `ground-plane@<camera>`. With neither, generation fails with
"buffer zone needs calibration", which the runner reports as a failing CI result.

**Layout.** X drives along the ground x axis at a constant speed (or stands still). Its box's
bottom edge, projected through the homography, is its ground footprint; the subject stands
`offset` metres from the footprint's midpoint along its normal, so its distance to the footprint
is exactly `offset` whatever the homography, and it moves along with X. The traces:

- zone pair: `buffer_m` -5 cm (violation) and +5 cm (clear), like every other zone edge;
- with `moving`: a speed pair just above / below `speed_mps_gt` (2.5%, at least 1 cm/s) well
  inside the buffer, and a stationary X;
- with `persist`: the persist pair, an interruption and an ID swap (`traces.id_swap_spec`'s
  per-track semantics); without it, an interruption and an ID swap that each still fire, since
  `inside` needs no duration.

Expected outcomes follow from the design (offset against `buffer_m`, speed against the
threshold, time against `persist`). Before a trace is returned, shapely re-measures every sample's
subject-to-footprint distance and the moving object's ground speed on the laid-out geometry and
checks them against the design, so a homography that bends the layout raises
`UnsupportedRuleError` instead of yielding a wrong expectation. The rules engine never decides.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from shapely.geometry import LineString, Point

from rulecam.contracts.core import Calibration, Track, TrackPoint
from rulecam.contracts.rule_ir import All, Inside, Moving, Predicate, RuleIR
from rulecam.perception.calibration import m_to_px, px_to_m
from rulecam.perception.errors import CalibrationError
from rulecam.rule_ci.geometry import (
    ANCHOR_SPACING_M,
    OBJECT_BOX_M,
    XY,
    ZONE_EDGE_EPS_M,
    UnsupportedRuleError,
    synthetic_calibration,
)
from rulecam.rule_ci.models import TraceCase
from rulecam.rule_ci.traces import (
    ENTER_S,
    EPOCH,
    EPS_FRACTION,
    FOOT_PARTS,
    HZ,
    LEAD_IN_S,
    MIN_SPEED_EPS_MPS,
    PERSON_BOX_M,
    STEP_S,
    TRACK_HOLD_S,
    Spec,
    above,
    below,
    id_swap_spec,
    pair_spec,
    part_of,
    time_eps,
)
from rulecam.rules.ir import parse_ref

HOLD_SAMPLES: Final = round(TRACK_HOLD_S * HZ)
_TOL: Final = 1e-9

Mapper = Callable[[Calibration, Sequence[XY]], list[XY]]


# --- shape --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BufferShape:
    """A buffer-zone rule's parts.

    Attributes:
        camera_id: The buffer zone's camera.
        zone: The buffer zone's name.
        subject: The concept that must not be inside the buffer.
        anchor: The buffer's concept (X).
        buffer_m: The buffer radius.
        moving: The `moving` conjunct, if any.
        moving_subject: Whether `moving` is about the subject (else about X).
        persist_s: The enclosing `persist`, if any.
    """

    camera_id: str
    zone: str
    subject: str
    anchor: str
    buffer_m: float
    moving: Moving | None
    moving_subject: bool
    persist_s: float | None


def buffer_shape(rule: RuleIR, node: Predicate, persist_s: float | None) -> BufferShape | None:
    """The parts of `inside(subject, buffer)` or `all(inside(subject, buffer), moving(...))`.

    Args:
        rule: The rule.
        node: The predicate under any `persist`.
        persist_s: The `persist` seconds, if any.

    Returns:
        The parts, or None when `node` is not an `inside` on a buffer zone (with at most one
        `moving` beside it under `all`).

    Raises:
        UnsupportedRuleError: It is that shape but cannot be laid out (incomplete buffer, a
            buffer of the subject itself, a `moving` about a third concept, a non-foot point).
    """
    kids: tuple[Predicate, ...] = node.of if isinstance(node, All) else (node,)
    insides = [k for k in kids if isinstance(k, Inside)]
    movings = [k for k in kids if isinstance(k, Moving)]
    if len(insides) != 1 or len(movings) > 1 or len(kids) != len(insides) + len(movings):
        return None
    inside = insides[0]
    spec = rule.zones.get(inside.zone)
    if spec is None or spec.kind != "buffer":
        return None
    subject, part = parse_ref(inside.object)
    if part not in FOOT_PARTS:
        msg = "inside a buffer zone is supported on an object's foot point"
        raise UnsupportedRuleError(msg)
    if not spec.buffer_of or spec.buffer_m is None or spec.buffer_m <= ZONE_EDGE_EPS_M:
        msg = f"buffer zone {inside.zone!r} needs buffer_of and buffer_m > {ZONE_EDGE_EPS_M:g} m"
        raise UnsupportedRuleError(msg)
    if spec.buffer_of == subject:
        msg = f"zone {inside.zone!r} is a buffer of the subject itself"
        raise UnsupportedRuleError(msg)
    moving = movings[0] if movings else None
    mover = parse_ref(moving.object)[0] if moving is not None else None
    if mover not in {None, subject, spec.buffer_of}:
        msg = f"moving({mover}) is about neither {subject!r} nor {spec.buffer_of!r}"
        raise UnsupportedRuleError(msg)
    return BufferShape(
        camera_id=spec.camera_id,
        zone=inside.zone,
        subject=subject,
        anchor=spec.buffer_of,
        buffer_m=spec.buffer_m,
        moving=moving,
        moving_subject=mover == subject,
        persist_s=persist_s,
    )


# --- ground plane -------------------------------------------------------------------------------


@dataclass(frozen=True)
class GroundPlane:
    """A camera's ground calibration, used to turn the metric layout into image pixels."""

    calibration: Calibration

    def to_px(self, points: Sequence[XY]) -> list[XY]:
        """Ground metres to image pixels."""
        return self._map(m_to_px, points)

    def to_m(self, points: Sequence[XY]) -> list[XY]:
        """Image pixels to ground metres."""
        return self._map(px_to_m, points)

    def _map(self, how: Mapper, points: Sequence[XY]) -> list[XY]:
        try:
            return how(self.calibration, points)
        except CalibrationError as exc:
            msg = f"the calibration of {self.calibration.camera_id!r} cannot place the trace: {exc}"
            raise UnsupportedRuleError(msg) from exc


def calibrated_cameras(rule: RuleIR) -> frozenset[str]:
    """The cameras the rule's `calibration` names (`ground-plane@cam1,ground-plane@cam2`)."""
    parts = (rule.calibration or "").split(",")
    return frozenset(part.rsplit("@", 1)[1] for part in parts if "@" in part)


def ground_plane(
    rule: RuleIR, shape: BufferShape, calibrations: Mapping[str, Calibration]
) -> GroundPlane:
    """The ground plane to lay a buffer rule's traces out on.

    Args:
        rule: The rule.
        shape: Its buffer parts.
        calibrations: Real calibrations by camera.

    Returns:
        The zone camera's real calibration if given, else the synthetic plane when the rule
        names a ground plane on that camera.

    Raises:
        UnsupportedRuleError: "buffer zone needs calibration": neither is available.
    """
    real = calibrations.get(shape.camera_id)
    if real is not None and real.camera_id == shape.camera_id:
        return GroundPlane(real)
    if shape.camera_id in calibrated_cameras(rule):
        return GroundPlane(synthetic_calibration(shape.camera_id))
    msg = (
        f"buffer zone needs calibration: zone {shape.zone!r} ({shape.buffer_m:g} m around "
        f"{shape.anchor!r}) is on camera {shape.camera_id!r}, which has no ground-plane "
        "calibration, so metres cannot be measured"
    )
    raise UnsupportedRuleError(msg)


# --- layout -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stay:
    """The subject keeps `offset` metres from X's footprint for `seconds` under `identity`."""

    offset: float
    seconds: float
    identity: int = 0


@dataclass(frozen=True)
class Sample:
    """One timeline sample of a laid-out trace.

    Attributes:
        block: Which stay (lead-in and tail included) the sample belongs to.
        identity: The subject's track identity.
        offset: The designed subject-to-footprint distance.
        anchor_m: X's foot point on the ground.
        anchor_px: X's foot point in the image.
        anchor_scale: Pixels per ground metre around X.
        footprint: X's ground footprint (the projected bottom edge of its box).
        subject_m: The subject's foot point on the ground.
        subject_px: The subject's foot point in the image.
        subject_scale: Pixels per ground metre around the subject.
    """

    block: int
    identity: int
    offset: float
    anchor_m: XY
    anchor_px: XY
    anchor_scale: float
    footprint: tuple[XY, XY]
    subject_m: XY
    subject_px: XY
    subject_scale: float


def _scales(plane: GroundPlane, ground: Sequence[XY]) -> list[float]:
    """Pixels per ground metre along the ground x axis around each point."""
    ends = plane.to_px([(x + dx, y) for x, y in ground for dx in (-0.5, 0.5)])
    return [math.dist(ends[2 * i], ends[2 * i + 1]) for i in range(len(ground))]


def _normal(a: XY, b: XY) -> XY:
    """Unit normal of segment `ab`, on the +y side."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length
    return (nx, ny) if ny >= 0 else (-nx, -ny)


@dataclass(frozen=True)
class BufferScene:
    """A subject keeping chosen distances from a (moving) X, on the rule's ground plane."""

    shape: BufferShape
    plane: GroundPlane

    @property
    def edge(self) -> tuple[float, float]:
        """`(inside, outside)` offsets `ZONE_EDGE_EPS_M` either side of the buffer edge."""
        return self.shape.buffer_m - ZONE_EDGE_EPS_M, self.shape.buffer_m + ZONE_EDGE_EPS_M

    @property
    def well(self) -> float:
        """An offset well inside the buffer."""
        return self.shape.buffer_m / 2

    @property
    def away(self) -> float:
        """An offset outside the buffer, even widened by 50%, for lead-in, tail and breaks."""
        return self.shape.buffer_m * 2 + 1

    def speeds(self) -> tuple[float, float]:
        """`(violating, clear)` speeds just either side of `moving`'s threshold (0 without)."""
        if self.shape.moving is None:
            return (0.0, 0.0)
        bound = self.shape.moving.speed_mps_gt
        eps = max(bound * EPS_FRACTION, MIN_SPEED_EPS_MPS)
        return (bound + eps, max(bound - eps, 0.0))

    def layout(self, stays: Sequence[Stay], speed: float) -> list[Sample]:
        """Lay out lead-in, `stays` and tail with X driving at `speed` along the ground x axis.

        Raises:
            UnsupportedRuleError: The calibration cannot place the trace.
        """
        steps = [Stay(self.away, LEAD_IN_S), *stays, Stay(self.away, STEP_S, stays[-1].identity)]
        rows = [(n, s) for n, s in enumerate(steps) for _ in range(round(s.seconds * HZ))]
        x0, y0 = ANCHOR_SPACING_M + self.shape.buffer_m, ANCHOR_SPACING_M
        anchor_m = [(x0 + speed * i / HZ, y0) for i in range(len(rows))]
        anchor_px = self.plane.to_px(anchor_m)
        anchor_scale = _scales(self.plane, anchor_m)
        half = [OBJECT_BOX_M[0] / 2 * s for s in anchor_scale]
        edges = self.plane.to_m(
            [
                (px[0] + side * h, px[1])
                for px, h in zip(anchor_px, half, strict=True)
                for side in (-1, 1)
            ]
        )
        footprints = [(edges[2 * i], edges[2 * i + 1]) for i in range(len(rows))]
        subject_m = []
        for (a, b), (_, stay) in zip(footprints, rows, strict=True):
            nx, ny = _normal(a, b)
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            subject_m.append((mid[0] + nx * stay.offset, mid[1] + ny * stay.offset))
        subject_px = self.plane.to_px(subject_m)
        subject_scale = _scales(self.plane, subject_m)
        return [
            Sample(
                block=block,
                identity=stay.identity,
                offset=stay.offset,
                anchor_m=anchor_m[i],
                anchor_px=anchor_px[i],
                anchor_scale=anchor_scale[i],
                footprint=footprints[i],
                subject_m=subject_m[i],
                subject_px=subject_px[i],
                subject_scale=subject_scale[i],
            )
            for i, (block, stay) in enumerate(rows)
        ]

    def check(self, samples: Sequence[Sample], speed: float) -> None:
        """Re-measure the laid-out geometry (shapely) against the design.

        Every sample's subject must be on the designed side of the buffer edge, and within each
        stay the moving object's ground speed on the designed side of the threshold.

        Raises:
            UnsupportedRuleError: The homography bent the layout across a threshold.
        """
        buffer_m = self.shape.buffer_m
        for sample in samples:
            measured = LineString(sample.footprint).distance(Point(sample.subject_m))
            if (measured <= buffer_m) != (sample.offset <= buffer_m):
                msg = (
                    f"the calibration bends the layout: {measured:.3f} m from the "
                    f"{self.shape.anchor} where {sample.offset:g} m was laid out"
                )
                raise UnsupportedRuleError(msg)
        if self.shape.moving is None:
            return
        bound = self.shape.moving.speed_mps_gt
        for prev, cur in itertools.pairwise(samples):
            if (prev.block, prev.identity) != (cur.block, cur.identity):
                continue
            measured = math.dist(self._mover(prev), self._mover(cur)) * HZ
            if (measured > bound) != (speed > bound):
                msg = (
                    f"the calibration bends the layout: {measured:.3f} m/s where {speed:g} m/s "
                    "was laid out"
                )
                raise UnsupportedRuleError(msg)

    def _mover(self, sample: Sample) -> XY:
        """The ground position of the object `moving` is about."""
        return sample.subject_m if self.shape.moving_subject else sample.anchor_m

    def trace(self, spec: Spec, samples: Sequence[Sample]) -> TraceCase:
        """Wrap laid-out samples into a `TraceCase`: one track per subject identity, plus X."""
        camera = self.shape.camera_id
        tracks = [
            _track(
                f"{camera}:{self.shape.subject}:{who + 1}",
                camera,
                self.shape.subject,
                [
                    _point(i, s.subject_m, s.subject_px, s.subject_scale, PERSON_BOX_M)
                    for i, s in enumerate(samples)
                    if s.identity == who
                ],
            )
            for who in sorted({s.identity for s in samples})
        ]
        tracks.append(
            _track(
                f"{camera}:{self.shape.anchor}:1",
                camera,
                self.shape.anchor,
                [
                    _point(i, s.anchor_m, s.anchor_px, s.anchor_scale, OBJECT_BOX_M)
                    for i, s in enumerate(samples)
                ],
            )
        )
        return TraceCase(
            name=spec.name,
            description=spec.description,
            kind=spec.kind,
            tracks=tuple(tracks),
            calibration=self.plane.calibration,
            t0=EPOCH,
            t1=EPOCH + timedelta(seconds=len(samples) / HZ),
            expected_violation=spec.expected,
            pair=spec.pair,
            value=spec.value,
        )

    def case(self, spec: Spec, stays: Sequence[Stay], speed: float) -> TraceCase:
        """Lay out, check and wrap one trace."""
        samples = self.layout(stays, speed)
        self.check(samples, speed)
        return self.trace(spec, samples)


def _point(index: int, ground: XY, px: XY, scale: float, box_m: XY) -> TrackPoint:
    """A track point whose box (`box_m` at `scale` px/m) stands on `px`."""
    width, height = box_m[0] * scale, box_m[1] * scale
    return TrackPoint(
        ts=EPOCH + timedelta(seconds=index / HZ),
        frame_idx=index,
        box_xyxy=(px[0] - width / 2, px[1] - height, px[0] + width / 2, px[1]),
        foot_px=px,
        foot_m=ground,
        confidence=0.95,
    )


def _track(track_id: str, camera_id: str, concept: str, points: Sequence[TrackPoint]) -> Track:
    return Track(track_id=track_id, camera_id=camera_id, concept=concept, points=tuple(points))


# --- traces -------------------------------------------------------------------------------------


def buffer_traces(shape: BufferShape, plane: GroundPlane) -> list[TraceCase]:
    """Boundary traces for a buffer-zone rule: zone, speed and persist pairs, plus variants.

    Args:
        shape: The rule's buffer parts (`buffer_shape`).
        plane: The ground plane to lay the traces out on (`ground_plane`).

    Returns:
        The traces, each with its expected outcome.

    Raises:
        UnsupportedRuleError: The calibration cannot place or bends the layout.
    """
    scene = BufferScene(shape, plane)
    fast, _ = scene.speeds()
    persist = shape.persist_s
    hold = ENTER_S if persist is None else above(persist, time_eps(persist))
    inner, outer = scene.edge
    what = f"{shape.anchor} (buffer {shape.buffer_m:g} m)"
    cases = [
        scene.case(
            Spec(
                f"zone_{side}",
                f"{hold:g} s at {offset:g} m from the {what}, just {side} its edge",
                "zone_edge",
                expected,
                pair="zone",
            ),
            [Stay(offset, hold)],
            fast,
        )
        for side, offset, expected in (("inside", inner, True), ("outside", outer, False))
    ]
    if shape.moving is not None:
        cases += _speed_traces(scene, hold)
    if persist is not None:
        return cases + _persist_traces(scene, persist, fast)
    return cases + _stay_variants(scene, fast)


def _speed_traces(scene: BufferScene, hold: float) -> list[TraceCase]:
    fast, slow = scene.speeds()
    mover = scene.shape.subject if scene.shape.moving_subject else scene.shape.anchor
    cases = [
        scene.case(
            pair_spec(
                "speed_mps_gt",
                v,
                "mps",
                expected=v == fast,
                kind="boundary",
                why=f"the {mover} moves at {v:g} m/s, {scene.well:g} m inside the buffer",
            ),
            [Stay(scene.well, hold)],
            v,
        )
        for v in (fast, slow)
    ]
    still = Spec(
        "stationary",
        f"the {mover} stands still, {scene.well:g} m inside the buffer",
        "boundary",
        False,
    )
    return [*cases, scene.case(still, [Stay(scene.well, hold)], 0.0)]


def _persist_traces(scene: BufferScene, persist: float, fast: float) -> list[TraceCase]:
    eps, part, well = time_eps(persist), part_of(persist), scene.well
    cases = [
        scene.case(
            pair_spec(
                "persist_s",
                s,
                "s",
                expected=s >= persist,
                kind="boundary",
                why=f"inside the buffer {s:g} s (persist {persist:g} s)",
            ),
            [Stay(well, s)],
            fast,
        )
        for s in (below(persist, eps), above(persist, eps))
    ]
    cases.append(
        scene.case(
            Spec(
                "interrupted_persist",
                f"two {part:g} s stays with a {STEP_S:g} s break",
                "interruption",
                False,
            ),
            [Stay(well, part), Stay(scene.away, STEP_S), Stay(well, part)],
            fast,
        )
    )
    if scene.shape.moving_subject:
        return cases  # the first ID's held last point stands still, so it is not `moving`
    stays = [Stay(well, part), Stay(well, part, 1)]
    samples = scene.layout(stays, fast)
    scene.check(samples, fast)
    held = _held_inside(samples, scene.shape.buffer_m)
    return [*cases, scene.trace(_id_swap(part, persist, held), samples)]


def _id_swap(part: float, persist: float, held: int) -> Spec:
    """`traces.id_swap_spec`, crediting only the held samples X has not driven away from."""
    if held == HOLD_SAMPLES:
        return id_swap_spec(part, persist)
    reached = part + held / HZ >= persist - _TOL
    return Spec(
        "id_swap_mid_persist",
        f"track ID changes after {part:g} s of {2 * part:g} s; X leaves the first ID's held "
        f"point after {held / HZ:g} s, so it lasts {part + held / HZ:g} s, "
        f"{'enough' if reached else 'too short'} for persist {persist:g} s",
        "id_swap",
        reached,
    )


def _held_inside(samples: Sequence[Sample], buffer_m: float) -> int:
    """How many of the first ID's held samples (its last point, frozen) stay in the buffer."""
    last = max(i for i, s in enumerate(samples) if s.identity == 0)
    held = Point(samples[last].subject_m)
    count = 0
    for sample in samples[last + 1 : last + 1 + HOLD_SAMPLES]:
        if LineString(sample.footprint).distance(held) > buffer_m + _TOL:
            break
        count += 1
    return count


def _stay_variants(scene: BufferScene, fast: float) -> list[TraceCase]:
    well = scene.well
    return [
        scene.case(
            Spec(
                "interrupted_stay",
                f"steps out of the buffer for {STEP_S:g} s and back; each stay fires",
                "interruption",
                True,
            ),
            [Stay(well, ENTER_S), Stay(scene.away, STEP_S), Stay(well, ENTER_S)],
            fast,
        ),
        scene.case(
            Spec(
                "id_swap_mid_stay",
                "track ID changes halfway through the stay; `inside` needs no history, so it fires",
                "id_swap",
                True,
            ),
            [Stay(well, ENTER_S), Stay(well, ENTER_S, 1)],
            fast,
        ),
    ]
