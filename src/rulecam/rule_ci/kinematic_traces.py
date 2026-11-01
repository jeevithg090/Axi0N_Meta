"""Kinematic and operator boundary traces for separation rules (WS1c, WS1d; task P2-08).

Shape: "keep X at least d m from (moving) Y", as `[persist] distance_m(X, Y, lt=d)` or
`[persist] inside(X, d m buffer of Y)`, alone or under `all` with `moving(X or Y)`. The distance
and buffer generators (`traces.proximity_traces`, `buffer_traces`) already straddle `d` with X
and Y moving side by side; these traces add the motion the near-miss (time-to-collision) metrics
describe, and the forklift-driver case:

- **approach pair** (no `persist`): the mover drives straight at the other, turns round with its
  closest gap at `d - eps` (violation) or `d + eps` (clear), and drives away;
- **pass-by pair** (no `persist`): the mover passes the other at a lateral offset `d - eps` /
  `d + eps`, the offset being the closest approach;
- **co-moving far away**: both move side by side 3.2 d apart (6.4 m for 2 m, the live WS1d
  candidate: `closest_m` 6.4, `ttc_s` none). It must never fire; an inverted bound (`gt`) does;
- **operator traces**, when X is person-like and Y vehicle-like (`rules.operators`): a person
  riding Y inside its box, `r = min(d/2, 0.5)` m from its footprint (must NOT fire: Y's operator),
  a pedestrian walking beside Y at the same distance, and one standing in front of it while it
  passes, inside its box but not moving with it (both MUST fire). The `drop_operator_exclusion`
  mutant (`rule_ci.mutations`) is killed by the riding traces.
- **slow close-up operator traces** (live P1-07, phone-23): Y moves just above `moving`'s
  threshold (or `SLOW_SPEED_MPS` without `moving`) with a box that fills the 720 px frame
  (`CLOSE_UP_BOX_M`): at 0.2 m/s it moves 3% of its box height per second in the image. A person
  riding it must NOT fire; a pedestrian it drives up to from `APPROACH_S` away, who stands
  inside its box once it arrives, MUST fire.

All traces use the synthetic camera (100 px = 1 m, `rule_ci.geometry`), also for a buffer rule
whose other traces go through a real homography: they test the rule's kinematics and the
operator logic (image boxes), not the camera. Y's ground footprint is the bottom edge of its box
(or its foot point with `.foot_point`), and every expected outcome follows from the layout, never
from running the engine. The pairs are kind `kinematic` and the operator traces kind `operator`.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from rulecam.contracts.core import Track
from rulecam.contracts.rule_ir import Moving, RuleIR
from rulecam.rule_ci.buffer_traces import BufferShape
from rulecam.rule_ci.geometry import OBJECT_BOX_M, XY
from rulecam.rule_ci.models import TraceCase
from rulecam.rule_ci.traces import (
    ENTER_S,
    EPS_FRACTION,
    HZ,
    LEAD_IN_S,
    MIN_DISTANCE_EPS_M,
    MIN_SPEED_EPS_MPS,
    PERSON_BOX_M,
    STEP_S,
    Proximity,
    Spec,
    above,
    finish,
    make_track,
    pair_spec,
    time_eps,
)
from rulecam.rules.ir import parse_ref
from rulecam.rules.operators import is_person_like, is_vehicle_like

ORIGIN: Final = (20.0, 10.0)
"""Where the static object stands (ground metres), clear of the frame edge."""
RUN_M: Final = 1.5
"""How far beyond the threshold an approach or pass starts and ends."""
FAR_FACTOR: Final = 3.2
NO_MOVING_SPEED_MPS: Final = 1.0
OPERATOR_SPEED_MPS: Final = 1.0
VEHICLE_BOX_M: Final = (2.0, 2.4)
"""A ridden vehicle's box: wide and tall enough to hold its operator."""
RIDER_BOX_M: Final = (0.6, 1.2)
RIDER_MAX_M: Final = 0.5
CLOSE_UP_BOX_M: Final = (4.0, 7.2)
"""A close-up vehicle's box: as tall as the 720 px frame (the live phone-23 forklift)."""
SLOW_SPEED_MPS: Final = 0.25
"""The slow traces' speed for a rule without `moving`."""
APPROACH_S: Final = 2.0
"""How long the slow vehicle drives before its box reaches the standing pedestrian."""


@dataclass(frozen=True)
class Separation:
    """A separation rule's parts.

    Attributes:
        camera_id: The rule's camera.
        subject: The concept that must keep away (X).
        other: The concept it keeps away from (Y).
        threshold_m: `d`.
        moving: The `moving` conjunct, if any.
        moving_subject: Whether `moving` is about X (else about Y).
        persist_s: The enclosing `persist`, if any.
        footprint: Whether distance is measured to Y's footprint (else its foot point).
        operator_pair: X is person-like and Y vehicle-like.
    """

    camera_id: str
    subject: str
    other: str
    threshold_m: float
    moving: Moving | None
    moving_subject: bool
    persist_s: float | None
    footprint: bool
    operator_pair: bool

    @property
    def eps(self) -> float:
        """How far either side of `d` a pair sits (2.5%, at least 2 cm)."""
        return max(self.threshold_m * EPS_FRACTION, MIN_DISTANCE_EPS_M)

    @property
    def speed(self) -> float:
        """The mover's speed: just above `moving`'s threshold, or 1 m/s without `moving`."""
        if self.moving is None:
            return NO_MOVING_SPEED_MPS
        bound = self.moving.speed_mps_gt
        return bound + max(bound * EPS_FRACTION, MIN_SPEED_EPS_MPS)

    @property
    def half_width(self) -> float:
        """Half the footprint segment Y's distance is measured to (0 for a foot point)."""
        return OBJECT_BOX_M[0] / 2 if self.footprint else 0.0


def _names(rule: RuleIR, concept: str) -> tuple[str, ...]:
    spec = rule.concepts.get(concept)
    return (concept,) if spec is None else (concept, spec.prompt)


def from_proximity(rule: RuleIR, scene: Proximity) -> Separation | None:
    """The separation parts of a `distance_m(lt=d)` rule (None for a `gt` bound)."""
    distance = scene.distance
    if distance.lt is None:
        return None
    subject, _ = parse_ref(distance.a)
    other, other_part = parse_ref(distance.b)
    moving = scene.moving
    return Separation(
        camera_id=scene.camera_id,
        subject=subject,
        other=other,
        threshold_m=distance.lt,
        moving=moving,
        moving_subject=moving is not None and parse_ref(moving.object)[0] == subject,
        persist_s=scene.persist_s,
        footprint=other_part != "foot_point",
        operator_pair=is_person_like(*_names(rule, subject))
        and is_vehicle_like(*_names(rule, other)),
    )


def from_buffer(rule: RuleIR, shape: BufferShape) -> Separation:
    """The separation parts of an `inside(X, buffer of Y)` rule."""
    return Separation(
        camera_id=shape.camera_id,
        subject=shape.subject,
        other=shape.anchor,
        threshold_m=shape.buffer_m,
        moving=shape.moving,
        moving_subject=shape.moving_subject,
        persist_s=shape.persist_s,
        footprint=True,
        operator_pair=is_person_like(*_names(rule, shape.subject))
        and is_vehicle_like(*_names(rule, shape.anchor)),
    )


Path = Callable[[int], XY]
"""Ground foot position at a sample index."""


@dataclass(frozen=True)
class _Actor:
    concept: str
    path: Path
    box_m: XY


def _case(sep: Separation, spec: Spec, actors: tuple[_Actor, _Actor], n_samples: int) -> TraceCase:
    tracks: list[Track] = [
        make_track(
            f"{sep.camera_id}:{actor.concept}:1",
            sep.camera_id,
            actor.concept,
            [(i, actor.path(i)) for i in range(n_samples)],
            actor.box_m,
        )
        for actor in actors
    ]
    return finish(spec, tracks, sep.camera_id, n_samples, 0.0)


def _static(point: XY) -> Path:
    return lambda _: point


def _roles(sep: Separation, mover: Path, still: XY) -> tuple[Path, Path]:
    """`(subject path, other path)`: the `moving` object moves, the other stands still."""
    if sep.moving_subject:
        return mover, _static(still)
    return _static(still), mover


def kinematic_traces(sep: Separation) -> list[TraceCase]:
    """Approach and pass-by pairs (without `persist`) and the co-moving far trace."""
    cases = [_co_moving_far(sep)]
    if sep.persist_s is None:
        cases = [*_approach_pair(sep), *_pass_pair(sep), *cases]
    return cases


def _approach_pair(sep: Separation) -> list[TraceCase]:
    """The mover drives along the ground x axis at the other, turns at gap `g`, drives away."""
    d, v = sep.threshold_m, sep.speed
    turn = math.ceil((d + RUN_M) / v * HZ)
    x0, y0 = ORIGIN
    cases = []
    for gap in (max(d - sep.eps, 0.0), d + sep.eps):
        # The subject faces the other's near footprint end; whichever moves keeps that gap.
        def mover(i: int, gap: float = gap) -> XY:
            travel = v * abs(i - turn) / HZ
            if sep.moving_subject:
                return (x0 + sep.half_width + gap + travel, y0)
            return (x0 - sep.half_width - gap - travel, y0)

        still = (x0, y0)
        subject, other = _roles(sep, mover, still)
        spec = pair_spec(
            "approach_lt",
            gap,
            "m",
            expected=gap < d,
            kind="kinematic",
            why=f"drives straight at it at {v:g} m/s and turns {gap:g} m away (threshold {d:g} m)",
        )
        cases.append(_case(sep, spec, _pair_actors(sep, subject, other), 2 * turn + 1))
    return cases


def _pass_pair(sep: Separation) -> list[TraceCase]:
    """The mover passes the other along x; the lateral offset is the closest approach."""
    d, v = sep.threshold_m, sep.speed
    middle = math.ceil((d + RUN_M + sep.half_width) / v * HZ)
    x0, y0 = ORIGIN
    cases = []
    for offset in (max(d - sep.eps, 0.0), d + sep.eps):

        def mover(i: int, offset: float = offset) -> XY:
            x = x0 + v * (i - middle) / HZ
            return (x, y0 + offset) if sep.moving_subject else (x, y0)

        still = (x0, y0) if sep.moving_subject else (x0, y0 + offset)
        subject, other = _roles(sep, mover, still)
        spec = pair_spec(
            "pass_lt",
            offset,
            "m",
            expected=offset < d,
            kind="kinematic",
            why=f"passes at {v:g} m/s, {offset:g} m to the side at the closest (threshold {d:g} m)",
        )
        cases.append(_case(sep, spec, _pair_actors(sep, subject, other), 2 * middle + 1))
    return cases


def _co_moving_far(sep: Separation) -> TraceCase:
    """Both move side by side, `FAR_FACTOR` x d apart: never a violation (live WS1d)."""
    d, v = sep.threshold_m, sep.speed
    far = round(max(FAR_FACTOR * d, d + 2 * RUN_M), 6)
    hold = ENTER_S if sep.persist_s is None else above(sep.persist_s, time_eps(sep.persist_s))
    n_samples = round((LEAD_IN_S + hold + STEP_S) * HZ)
    x0, y0 = ORIGIN

    def other(i: int) -> XY:
        return (x0 + v * i / HZ, y0)

    def subject(i: int) -> XY:
        return (x0 + v * i / HZ, y0 + far)

    spec = Spec(
        f"co_moving_far_{far:g}m",
        f"moves alongside it at {v:g} m/s, {far:g} m away: closest {far:g} m, no collision "
        f"course, never closer than {d:g} m",
        "kinematic",
        False,
    )
    return _case(sep, spec, _pair_actors(sep, subject, other), n_samples)


def _pair_actors(sep: Separation, subject: Path, other: Path) -> tuple[_Actor, _Actor]:
    return (
        _Actor(sep.subject, subject, PERSON_BOX_M),
        _Actor(sep.other, other, OBJECT_BOX_M),
    )


# --- operator vs pedestrian ---------------------------------------------------------------------


def operator_traces(sep: Separation) -> list[TraceCase]:
    """Riding (must not fire) against pedestrians at the same distance (must fire).

    Only for person-vehicle rules measured to the vehicle's footprint.
    """
    if not sep.operator_pair or not sep.footprint:
        return []
    return [*_operator_traces(sep), *_slow_operator_traces(sep)]


def _operator_traces(sep: Separation) -> list[TraceCase]:
    r = _rider_offset(sep)
    v = max(sep.speed, OPERATOR_SPEED_MPS)
    n_samples = _hold_samples(sep)
    x0, y0 = ORIGIN
    half = VEHICLE_BOX_M[0] / 2

    def vehicle(i: int) -> XY:
        return (x0 + v * i / HZ, y0)

    def rider(i: int) -> XY:
        return (x0 + v * i / HZ, y0 - r)

    def beside(i: int) -> XY:
        return (x0 + v * i / HZ + half + r, y0)

    layouts: list[tuple[Spec, Path]] = [
        (
            Spec(
                f"operator_riding_{r:g}m",
                f"rides the {sep.other} inside its box at {v:g} m/s, {r:g} m from its footprint: "
                "its operator, not a pedestrian",
                "operator",
                False,
                pair="operator_vs_pedestrian",
            ),
            rider,
        ),
        (
            Spec(
                f"pedestrian_beside_{r:g}m",
                f"walks beside the {sep.other} at {v:g} m/s, {r:g} m from its footprint, outside "
                "its box",
                "operator",
                True,
                pair="operator_vs_pedestrian",
            ),
            beside,
        ),
    ]
    if sep.persist_s is None and not sep.moving_subject:
        front = (x0 + v * (n_samples // 2) / HZ, y0 + r)
        layouts.append(
            (
                Spec(
                    f"pedestrian_in_front_{r:g}m",
                    f"stands {r:g} m in front of the passing {sep.other}, inside its box but "
                    "not moving with it",
                    "operator",
                    True,
                    pair="operator_vs_pedestrian",
                ),
                _static(front),
            )
        )
    return [
        _operator_case(sep, spec, path, vehicle, box=VEHICLE_BOX_M, n_samples=n_samples)
        for spec, path in layouts
    ]


def _slow_operator_traces(sep: Separation) -> list[TraceCase]:
    """The live close-up: a slow vehicle whose box fills the frame, ridden or driving up."""
    r = _rider_offset(sep)
    v = sep.speed if sep.moving is not None else SLOW_SPEED_MPS
    x0, y0 = ORIGIN

    def vehicle(i: int) -> XY:
        return (x0 + v * i / HZ, y0)

    def rider(i: int) -> XY:
        return (x0 + v * i / HZ, y0 - r)

    riding = Spec(
        f"operator_riding_slow_{r:g}m",
        f"rides the {sep.other} at {v:g} m/s filmed close up (its box fills the frame, so it "
        f"moves {v / CLOSE_UP_BOX_M[1]:.1%} of its height per second in the image), {r:g} m "
        "from its footprint: its operator, not a pedestrian",
        "operator",
        False,
        pair="operator_vs_pedestrian",
    )
    cases = [
        _operator_case(
            sep, riding, rider, vehicle, box=CLOSE_UP_BOX_M, n_samples=_hold_samples(sep)
        )
    ]
    if sep.persist_s is None and not sep.moving_subject:
        reach = CLOSE_UP_BOX_M[0] / 2 + RIDER_BOX_M[0] / 2  # front edge to the person's centre
        front = (x0 + reach + v * APPROACH_S, y0 + r)
        n_samples = math.ceil((APPROACH_S + reach / v + ENTER_S) * HZ)
        approach = Spec(
            f"pedestrian_slow_approach_{r:g}m",
            f"stands {r:g} m in front of the close-up {sep.other} that drives up to them at "
            f"{v:g} m/s and over them: outside its box first, never moving with it",
            "operator",
            True,
            pair="operator_vs_pedestrian",
        )
        cases.append(
            _operator_case(
                sep, approach, _static(front), vehicle, box=CLOSE_UP_BOX_M, n_samples=n_samples
            )
        )
    return cases


def _rider_offset(sep: Separation) -> float:
    return round(min(sep.threshold_m / 2, RIDER_MAX_M), 6)


def _hold_samples(sep: Separation) -> int:
    hold = ENTER_S if sep.persist_s is None else above(sep.persist_s, time_eps(sep.persist_s))
    return round((LEAD_IN_S + hold + STEP_S) * HZ)


def _operator_case(
    sep: Separation, spec: Spec, person: Path, vehicle: Path, *, box: XY, n_samples: int
) -> TraceCase:
    actors = (_Actor(sep.subject, person, RIDER_BOX_M), _Actor(sep.other, vehicle, box))
    return _case(sep, spec, actors, n_samples)
