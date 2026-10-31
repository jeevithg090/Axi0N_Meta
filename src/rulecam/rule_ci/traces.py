"""Boundary traces: synthetic tracks built from a rule's own thresholds (P2-08, MP §1, §3.4).

Still images cannot test dwell, sequence or distance logic, so Rule CI feeds the real rules
engine synthetic tracks that straddle each threshold of the rule:

- every numeric threshold gets a just-below / just-above pair (dwell 19.5/20.5 s for 20 s,
  distance 1.95/2.05 m for 2 m, persist 0.9/1.1 s for 1 s: 2.5% either side, at least one
  sample or 2 cm);
- `max_gap_s` gets a gap pair at `max_gap_s` +- 1 s; `require_within_s` a window pair;
- an interruption mid-event, an ID swap mid-episode and a sequence-order variant;
- every zone gets a just-inside / just-outside pair, 5 cm either side of one of its edges.

Each trace's expected outcome follows from how it was built (e.g. "washed 19.5 s of a required
20 s, then entered the line: violation"), never from running the engine. Rules are evaluated per
track, so an ID swap loses the subject's history: its expected outcome is what the per-track
semantics give (a missed wash credit fires, a split persist does not).

Supported shapes: `sequence(inside -> dwell)` (past or future), `[persist] inside`, and
`[persist] distance_m` alone or with `moving` under `all`. Anything else raises
`UnsupportedRuleError`, which the runner reports as a CI failure, never as a pass. Everything is
deterministic: no randomness, a fixed epoch and a 10 Hz timeline.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from rulecam.contracts.core import Track, TrackPoint
from rulecam.contracts.rule_ir import (
    All,
    DistanceM,
    Dwell,
    Inside,
    Moving,
    Persist,
    Predicate,
    RuleIR,
)
from rulecam.contracts.rule_ir import Sequence as SequenceNode
from rulecam.rule_ci.geometry import (
    OBJECT_BOX_M,
    PX_PER_M,
    XY,
    Region,
    UnsupportedRuleError,
    far_point,
    layout_regions,
    pick_edge,
    synthetic_calibration,
)
from rulecam.rule_ci.models import TraceCase, TraceKind
from rulecam.rules.ir import children, parse_ref

EPOCH: Final = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
HZ: Final = 10.0
LEAD_IN_S: Final = 2.0
STEP_S: Final = 1.0
ENTER_S: Final = 2.0
EPS_FRACTION: Final = 0.025
MIN_DISTANCE_EPS_M: Final = 0.02
MIN_SPEED_EPS_MPS: Final = 0.01
GAP_DELTA_S: Final = 1.0
PART_FRACTION: Final = 0.6
"""Each half of a split event lasts 60% of the threshold: alone too short, together enough."""
TRACK_HOLD_S: Final = 0.5
"""A track's last point is held this long after it ends (rules README, "Timeline")."""
HAND_DROP_M: Final = 1.0
PERSON_BOX_M: Final = (1.0, 2.0)
HAND_BOX_M: Final = (0.2, 0.2)
DEFAULT_CAMERA: Final = "cam1"
_FOOT_PARTS: Final = (None, "foot_point")


# --- quantities ---------------------------------------------------------------------------------


def time_eps(seconds: float) -> float:
    """How far either side of a time threshold the boundary pair sits (2.5%, at least 1 sample)."""
    return max(seconds * EPS_FRACTION, 1 / HZ)


def below(value: float, eps: float) -> float:
    """`value - eps` rounded down to the sample grid (never negative)."""
    return max(math.floor((value - eps) * HZ + 1e-9) / HZ, 0.0)


def above(value: float, eps: float) -> float:
    """`value + eps` rounded up to the sample grid."""
    return math.ceil((value + eps) * HZ - 1e-9) / HZ


def part_of(threshold_s: float) -> float:
    """One half of a split event: `PART_FRACTION` of the threshold, on the sample grid."""
    return above(threshold_s * PART_FRACTION, 0.0)


# --- tracks -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Phase:
    """The subject stands at `place` (its foot point) for `seconds` under track `identity`."""

    place: XY
    seconds: float
    identity: int = 0


Sample = tuple[XY, int]


def expand(phases: Sequence[Phase]) -> list[Sample]:
    """One `(foot, identity)` per timeline sample."""
    return [(p.place, p.identity) for p in phases for _ in range(round(p.seconds * HZ))]


def _point(index: int, foot: XY, box_m: tuple[float, float]) -> TrackPoint:
    fx, fy = foot[0] * PX_PER_M, foot[1] * PX_PER_M
    width, height = box_m[0] * PX_PER_M, box_m[1] * PX_PER_M
    return TrackPoint(
        ts=EPOCH + timedelta(seconds=index / HZ),
        frame_idx=index,
        box_xyxy=(fx - width / 2, fy - height, fx + width / 2, fy),
        foot_px=(fx, fy),
        foot_m=foot,
        confidence=0.95,
    )


def make_track(
    track_id: str, camera_id: str, concept: str, feet: Sequence[tuple[int, XY]], box_m: XY
) -> Track:
    """A track with one point per `(sample index, foot)` and a box of `box_m` above the foot."""
    points = tuple(_point(index, foot, box_m) for index, foot in feet)
    return Track(track_id=track_id, camera_id=camera_id, concept=concept, points=points)


def subject_tracks(
    camera_id: str, concept: str, samples: Sequence[Sample], *, hands: bool, box_m: XY
) -> list[Track]:
    """The subject's tracks, one per identity, plus one continuous hands track if `hands`.

    The hands' box is centred `HAND_DROP_M` above the foot, inside the person's box, so the
    engine associates them with whichever identity the person has.
    """
    tracks = [
        make_track(
            f"{camera_id}:{concept}:{identity + 1}",
            camera_id,
            concept,
            [(i, foot) for i, (foot, who) in enumerate(samples) if who == identity],
            box_m,
        )
        for identity in sorted({who for _, who in samples})
    ]
    if hands:
        half = HAND_BOX_M[1] / 2
        feet = [(i, (x, y - HAND_DROP_M + half)) for i, ((x, y), _) in enumerate(samples)]
        tracks.append(make_track(f"{camera_id}:hands:1", camera_id, "hands", feet, HAND_BOX_M))
    return tracks


def anchor_tracks(camera_id: str, regions: dict[str, Region], n_samples: int) -> list[Track]:
    """A static track for each buffer zone's concept, present for the whole trace."""
    return [
        make_track(
            f"{camera_id}:{region.anchor_concept}:{number}",
            camera_id,
            region.anchor_concept,
            [(i, region.anchor) for i in range(n_samples)],
            OBJECT_BOX_M,
        )
        for number, region in enumerate(regions.values(), start=1)
        if region.anchor is not None and region.anchor_concept is not None
    ]


@dataclass(frozen=True)
class Spec:
    """What a trace is and what it must do (`expected` is the violation flag)."""

    name: str
    description: str
    kind: TraceKind
    expected: bool
    pair: str | None = None
    value: float | None = None


def finish(
    spec: Spec, tracks: Sequence[Track], camera_id: str, n_samples: int, horizon_s: float
) -> TraceCase:
    """Wrap tracks into a `TraceCase` whose window covers the trace plus `horizon_s`."""
    return TraceCase(
        name=spec.name,
        description=spec.description,
        kind=spec.kind,
        tracks=tuple(tracks),
        calibration=synthetic_calibration(camera_id),
        t0=EPOCH,
        t1=EPOCH + timedelta(seconds=n_samples / HZ + horizon_s),
        expected_violation=spec.expected,
        pair=spec.pair,
        value=spec.value,
    )


@dataclass(frozen=True)
class ZoneScene:
    """A subject moving between places in a rule's zones (the zone-based shapes).

    Attributes:
        camera_id: The zones' camera.
        concept: The subject's concept.
        hands: Whether the subject is drawn with hands (the requirement uses `.hands`).
        regions: The rule's zones laid out on the synthetic camera.
        far: A place outside every zone.
        horizon_s: Extra window after the trace (future sequences decide at a deadline).
    """

    camera_id: str
    concept: str
    hands: bool
    regions: dict[str, Region]
    far: XY
    horizon_s: float = 0.0

    def case(self, spec: Spec, phases: Sequence[Phase]) -> TraceCase:
        """Build a trace: lead-in far away, the phases, a short tail far away."""
        last = phases[-1].identity if phases else 0
        samples = expand([Phase(self.far, LEAD_IN_S), *phases, Phase(self.far, STEP_S, last)])
        box = PERSON_BOX_M if self.hands else OBJECT_BOX_M
        tracks = subject_tracks(self.camera_id, self.concept, samples, hands=self.hands, box_m=box)
        tracks += anchor_tracks(self.camera_id, self.regions, len(samples))
        return finish(spec, tracks, self.camera_id, len(samples), self.horizon_s)


# --- shape: sequence(inside -> dwell) -----------------------------------------------------------


@dataclass(frozen=True)
class SequencePlaces:
    """Where the subject goes in a sequence rule (feet), and the zone-edge pairs."""

    line: XY
    sink: XY
    line_edge: tuple[XY, XY]
    sink_edge: tuple[XY, XY]


def _sequence_places(trigger: Region, requirement: Region, hands: bool) -> SequencePlaces:
    drop = HAND_DROP_M if hands else 0.0

    def ref(foot: XY) -> XY:
        return (foot[0], foot[1] - drop)

    def foot(point: XY) -> XY:
        return (point[0], point[1] + drop)

    line, sink = trigger.inside(), foot(requirement.inside())
    if not trigger.contains(line) or requirement.contains(ref(line)) or trigger.contains(sink):
        msg = f"zones {trigger.name!r} and {requirement.name!r} overlap; no clear layout"
        raise UnsupportedRuleError(msg)
    line_edge = pick_edge(trigger, lambda p: not requirement.contains(ref(p)))
    sink_in, sink_out = pick_edge(requirement, lambda p: not trigger.contains(foot(p)))
    return SequencePlaces(line, sink, line_edge, (foot(sink_in), foot(sink_out)))


def _sequence_scene(rule: RuleIR, node: SequenceNode) -> tuple[ZoneScene, Inside, Dwell]:
    trigger, requirement = node.trigger, node.requirement
    if not isinstance(trigger, Inside) or not isinstance(requirement, Dwell):
        msg = "sequence rules are supported as sequence(trigger=inside, requirement=dwell)"
        raise UnsupportedRuleError(msg)
    concept, trigger_part = parse_ref(trigger.object)
    req_concept, req_part = parse_ref(requirement.object)
    if trigger_part not in _FOOT_PARTS or req_concept != concept or req_part == "mask":
        msg = "the trigger and requirement must be about the same subject's foot point or hands"
        raise UnsupportedRuleError(msg)
    regions = layout_regions(dict(rule.zones), [trigger.zone, requirement.zone])
    scene = ZoneScene(
        camera_id=rule.zones[trigger.zone].camera_id,
        concept=concept,
        hands=req_part == "hands",
        regions=regions,
        far=far_point(regions),
        horizon_s=node.require_within_s + STEP_S if node.semantics == "future" else 0.0,
    )
    return scene, trigger, requirement


def sequence_traces(rule: RuleIR, node: SequenceNode) -> list[TraceCase]:
    """Boundary traces for `sequence(inside(subject, A) -> dwell(subject[.hands], B))`."""
    scene, trigger, requirement = _sequence_scene(rule, node)
    places = _sequence_places(
        scene.regions[trigger.zone], scene.regions[requirement.zone], scene.hands
    )
    if node.semantics == "past":
        return _past_traces(scene, places, requirement, node.require_within_s)
    return _future_traces(scene, places, requirement, node.require_within_s)


@dataclass(frozen=True)
class _Wash:
    """Requirement durations derived from the dwell thresholds."""

    short: float
    enough: float
    part: float
    gaps: tuple[tuple[float, bool], ...]  # (gap seconds, merged into one visit?)

    @classmethod
    def of(cls, dwell: Dwell) -> _Wash:
        eps = time_eps(dwell.total_s_gte)
        gap = dwell.max_gap_s
        gaps: list[tuple[float, bool]] = []
        if gap > 0:
            gaps.append((below(gap, min(GAP_DELTA_S, gap / 2)), True))
        gaps.append((above(gap, GAP_DELTA_S), False))
        return cls(
            short=below(dwell.total_s_gte, eps),
            enough=above(dwell.total_s_gte, eps),
            part=part_of(dwell.total_s_gte),
            gaps=tuple(g for g in gaps if g[0] > 0),
        )


def _pair(base: str, value: float, unit: str, *, expected: bool, kind: TraceKind, why: str) -> Spec:
    return Spec(
        name=f"{base}_{value:g}{unit}",
        description=why,
        kind=kind,
        expected=expected,
        pair=base,
        value=value,
    )


def _around_entry(
    scene: ZoneScene, at: SequencePlaces, activity: Sequence[Phase], *, future: bool
) -> list[Phase]:
    """Washing before entering the line (past semantics) or after it (future)."""
    step, enter = Phase(scene.far, STEP_S), Phase(at.line, ENTER_S)
    return [enter, step, *activity] if future else [*activity, step, enter]


def _requirement_traces(
    scene: ZoneScene, at: SequencePlaces, dwell: Dwell, *, future: bool
) -> list[TraceCase]:
    """The dwell pair, the gap pair and an interruption, around one line entry."""
    wash, threshold = _Wash.of(dwell), dwell.total_s_gte
    when = "after entering" if future else "before entering"
    cases = [
        scene.case(
            _pair(
                "total_s_gte",
                s,
                "s",
                expected=s < threshold,
                kind="boundary",
                why=f"washes {s:g} s of {threshold:g} s {when}",
            ),
            _around_entry(scene, at, [Phase(at.sink, s)], future=future),
        )
        for s in (wash.short, wash.enough)
    ]
    cases += [
        scene.case(
            _pair(
                "max_gap_s",
                gap,
                "s",
                expected=not merged,
                kind="gap",
                why=f"two {wash.part:g} s washes {gap:g} s apart (max gap "
                f"{dwell.max_gap_s:g} s) {when}",
            ),
            _around_entry(
                scene,
                at,
                [Phase(at.sink, wash.part), Phase(scene.far, gap), Phase(at.sink, wash.part)],
                future=future,
            ),
        )
        for gap, merged in wash.gaps
    ]
    halfway = [Phase(at.sink, below(threshold / 2, 0.0))]
    cases.append(
        scene.case(
            Spec("interrupted_wash", f"wash stopped halfway {when}", "interruption", True),
            _around_entry(scene, at, halfway, future=future),
        )
    )
    return cases + _edge_traces(scene, at, wash, after_entry=future)


def _past_traces(
    scene: ZoneScene, at: SequencePlaces, dwell: Dwell, within_s: float
) -> list[TraceCase]:
    wash, far = _Wash.of(dwell), scene.far
    eps = time_eps(within_s)
    waits = [(below(within_s, eps + 1 / HZ), False), (above(within_s, eps), True)]
    cases = [
        scene.case(
            _pair(
                "require_within_s",
                wait,
                "s",
                expected=late,
                kind="window",
                why=f"washes, waits {wait:g} s (window {within_s:g} s), then enters",
            ),
            [Phase(at.sink, wash.enough), Phase(far, wait), Phase(at.line, ENTER_S)],
        )
        for wait, late in waits
        if wait > 0
    ]
    return [
        *_requirement_traces(scene, at, dwell, future=False),
        *cases,
        scene.case(
            Spec(
                "id_swap_mid_wash",
                "track ID changes halfway through the wash; the new ID has no full wash, so "
                "entering fires",
                "id_swap",
                True,
            ),
            [
                Phase(at.sink, wash.part),
                Phase(at.sink, wash.part, 1),
                Phase(far, STEP_S, 1),
                Phase(at.line, ENTER_S, 1),
            ],
        ),
        scene.case(
            Spec("enters_before_washing", "enters first, washes afterwards", "order", True),
            [Phase(at.line, ENTER_S), Phase(far, STEP_S), Phase(at.sink, wash.enough)],
        ),
    ]


def _future_traces(
    scene: ZoneScene, at: SequencePlaces, dwell: Dwell, within_s: float
) -> list[TraceCase]:
    wash, far, threshold = _Wash.of(dwell), scene.far, dwell.total_s_gte
    enter = Phase(at.line, ENTER_S)
    eps = time_eps(within_s) + 1 / HZ
    slack = within_s - ENTER_S - threshold  # the longest wait that still allows a full wash
    waits = [(below(slack - (wash.enough - threshold), eps), False), (above(slack, eps), True)]
    cases = [
        scene.case(
            _pair(
                "require_within_s",
                wait,
                "s",
                expected=late,
                kind="window",
                why=f"enters, waits {wait:g} s, then washes (window {within_s:g} s)",
            ),
            [enter, Phase(far, wait), Phase(at.sink, wash.enough)],
        )
        for wait, late in waits
        if wait > 0
    ]
    return [
        *_requirement_traces(scene, at, dwell, future=True),
        *cases,
        scene.case(
            Spec(
                "id_swap_after_entry",
                "track ID changes after entering; the entering ID never washes, so the "
                "deadline fires",
                "id_swap",
                True,
            ),
            [enter, Phase(far, STEP_S), Phase(far, STEP_S, 1), Phase(at.sink, wash.enough, 1)],
        ),
        scene.case(
            Spec("washes_before_entering", "washes first, enters afterwards", "order", True),
            [Phase(at.sink, wash.enough), Phase(far, STEP_S), enter],
        ),
    ]


def _edge_traces(
    scene: ZoneScene, at: SequencePlaces, wash: _Wash, *, after_entry: bool
) -> list[TraceCase]:
    step = Phase(scene.far, STEP_S)
    line_in, line_out = at.line_edge
    sink_in, sink_out = at.sink_edge
    cases = [
        scene.case(
            Spec(
                f"trigger_zone_{side}",
                f"goes just {side} the trigger zone edge without washing",
                "zone_edge",
                inside,
                pair="trigger_zone",
            ),
            [Phase(place, ENTER_S)],
        )
        for side, place, inside in (("inside", line_in, True), ("outside", line_out, False))
    ]
    for side, place, inside in (("inside", sink_in, True), ("outside", sink_out, False)):
        spec = Spec(
            f"requirement_zone_{side}",
            f"washes {wash.enough:g} s just {side} the requirement zone edge",
            "zone_edge",
            not inside,
            pair="requirement_zone",
        )
        washing, enter = Phase(place, wash.enough), Phase(at.line, ENTER_S)
        cases.append(
            scene.case(spec, [enter, step, washing] if after_entry else [washing, step, enter])
        )
    return cases


def _id_swap_spec(part: float, persist_s: float) -> Spec:
    """An ID swap halfway through a persist, with its per-track expected outcome.

    Only the first ID's part, plus the `TRACK_HOLD_S` its last point is held for, can reach the
    threshold.
    """
    reached = part + TRACK_HOLD_S >= persist_s
    return Spec(
        "id_swap_mid_persist",
        f"track ID changes after {part:g} s of {2 * part:g} s; the first ID lasts "
        f"{part + TRACK_HOLD_S:g} s with the hold, {'enough' if reached else 'too short'} for "
        f"persist {persist_s:g} s",
        "id_swap",
        reached,
    )


# --- shape: [persist] inside --------------------------------------------------------------------


def inside_traces(rule: RuleIR, node: Inside, persist_s: float | None) -> list[TraceCase]:
    """Boundary traces for `inside(object, zone)`, optionally under `persist`."""
    concept, part = parse_ref(node.object)
    if part not in _FOOT_PARTS:
        msg = "inside is supported on an object's foot point"
        raise UnsupportedRuleError(msg)
    regions = layout_regions(dict(rule.zones), [node.zone])
    region = regions[node.zone]
    if region.anchor_concept == concept:
        msg = f"zone {node.zone!r} is a buffer of the subject itself"
        raise UnsupportedRuleError(msg)
    scene = ZoneScene(
        camera_id=rule.zones[node.zone].camera_id,
        concept=concept,
        hands=False,
        regions=regions,
        far=far_point(regions),
    )
    inside, outside = pick_edge(region, lambda _: True)
    hold = ENTER_S if persist_s is None else above(persist_s, time_eps(persist_s))
    cases = [
        scene.case(
            Spec(
                f"zone_{side}",
                f"{hold:g} s just {side} the edge of {node.zone!r}",
                "zone_edge",
                expected,
                pair="zone",
            ),
            [Phase(place, hold)],
        )
        for side, place, expected in (("inside", inside, True), ("outside", outside, False))
    ]
    if persist_s is not None:
        cases += _persist_traces(scene, region.inside(), persist_s)
    return cases


def _persist_traces(scene: ZoneScene, place: XY, persist_s: float) -> list[TraceCase]:
    eps, part = time_eps(persist_s), part_of(persist_s)
    cases = [
        scene.case(
            _pair(
                "persist_s",
                s,
                "s",
                expected=s >= persist_s,
                kind="boundary",
                why=f"in the zone {s:g} s (persist {persist_s:g} s)",
            ),
            [Phase(place, s)],
        )
        for s in (below(persist_s, eps), above(persist_s, eps))
    ]
    return [
        *cases,
        scene.case(
            Spec(
                "interrupted_persist",
                f"two {part:g} s stays with a {STEP_S:g} s break",
                "interruption",
                False,
            ),
            [Phase(place, part), Phase(scene.far, STEP_S), Phase(place, part)],
        ),
        scene.case(
            _id_swap_spec(part, persist_s),
            [Phase(place, part), Phase(place, part, 1)],
        ),
    ]


# --- shape: [persist] distance_m [and moving] ---------------------------------------------------


@dataclass(frozen=True)
class Proximity:
    """A subject `a` that moves alongside `b` at a lateral offset (the distance under test).

    `a` stands directly below the middle of `b`'s footprint, so the ground distance equals the
    offset, and both move at the trace's speed, so `moving` measures the same for either.
    """

    camera_id: str
    distance: DistanceM
    moving: Moving | None
    persist_s: float | None

    @property
    def near(self) -> tuple[float, float]:
        """`(violating, clear)` offsets just either side of the distance threshold."""
        lt, gt = self.distance.lt, self.distance.gt
        bound = lt if lt is not None else (gt or 0.0)
        eps = max(bound * EPS_FRACTION, MIN_DISTANCE_EPS_M)
        lo, hi = max(bound - eps, 0.0), bound + eps
        return (lo, hi) if self.distance.lt is not None else (hi, lo)

    @property
    def away(self) -> float:
        """An offset well on the clear side, for lead-in, tail and breaks."""
        if self.distance.lt is not None:
            return self.distance.lt * 2 + 1
        return (self.distance.gt or 0.0) / 2

    def speeds(self) -> tuple[float, float]:
        """`(violating, clear)` speeds just either side of `moving`'s threshold."""
        if self.moving is None:
            return (0.0, 0.0)
        bound = self.moving.speed_mps_gt
        eps = max(bound * EPS_FRACTION, MIN_SPEED_EPS_MPS)
        return (bound + eps, max(bound - eps, 0.0))

    def case(
        self, spec: Spec, phases: Sequence[tuple[float, float, int]], speed: float
    ) -> TraceCase:
        """Build a trace from `(offset, seconds, identity)` phases at a constant speed."""
        steps = [(self.away, LEAD_IN_S, 0), *phases, (self.away, STEP_S, phases[-1][2])]
        rows = [(offset, who) for offset, seconds, who in steps for _ in range(round(seconds * HZ))]
        a_concept, _ = parse_ref(self.distance.a)
        b_concept, _ = parse_ref(self.distance.b)
        b_feet = [(i, (1.0 + speed * i / HZ, 2.0)) for i in range(len(rows))]
        a_samples = [
            ((x, y + offset), who) for (_, (x, y)), (offset, who) in zip(b_feet, rows, strict=True)
        ]
        tracks = subject_tracks(
            self.camera_id, a_concept, a_samples, hands=False, box_m=PERSON_BOX_M
        )
        tracks.append(
            make_track(
                f"{self.camera_id}:{b_concept}:1", self.camera_id, b_concept, b_feet, OBJECT_BOX_M
            )
        )
        return finish(spec, tracks, self.camera_id, len(rows), 0.0)


def proximity_parts(node: Predicate) -> tuple[DistanceM, Moving | None] | None:
    """`(distance, moving)` if `node` is `distance_m` or `all(distance_m[, moving])`."""
    kids: tuple[Predicate, ...] = node.of if isinstance(node, All) else (node,)
    distances = [k for k in kids if isinstance(k, DistanceM)]
    movings = [k for k in kids if isinstance(k, Moving)]
    if len(distances) != 1 or len(movings) > 1 or len(kids) != len(distances) + len(movings):
        return None
    distance = distances[0]
    if (distance.lt is None) == (distance.gt is None):
        return None
    concepts = {parse_ref(distance.a)[0], parse_ref(distance.b)[0]}
    if len(concepts) == 1 or (movings and parse_ref(movings[0].object)[0] not in concepts):
        return None
    if parse_ref(distance.a)[1] == "hands":
        return None
    return distance, (movings[0] if movings else None)


def proximity_traces(scene: Proximity) -> list[TraceCase]:
    """Boundary traces for a distance rule: distance, speed and persist pairs, plus variants."""
    hit, clear = scene.near
    fast, slow = scene.speeds()
    persist = scene.persist_s
    hold = ENTER_S if persist is None else above(persist, time_eps(persist))
    op = "lt" if scene.distance.lt is not None else "gt"
    cases = [
        scene.case(
            _pair(
                f"distance_{op}",
                d,
                "m",
                expected=d == hit,
                kind="boundary",
                why=f"{d:g} m apart for {hold:g} s",
            ),
            [(d, hold, 0)],
            fast,
        )
        for d in (hit, clear)
    ]
    if scene.moving is not None:
        cases += [
            scene.case(
                _pair(
                    "speed_mps_gt",
                    v,
                    "mps",
                    expected=v == fast,
                    kind="boundary",
                    why=f"moving {v:g} m/s, {hit:g} m apart",
                ),
                [(hit, hold, 0)],
                v,
            )
            for v in (fast, slow)
        ]
    if persist is not None:
        cases += _proximity_persist(scene, persist, hit, fast)
    return cases


def _proximity_persist(
    scene: Proximity, persist: float, hit: float, fast: float
) -> list[TraceCase]:
    eps, part = time_eps(persist), part_of(persist)
    cases = [
        scene.case(
            _pair(
                "persist_s",
                s,
                "s",
                expected=s >= persist,
                kind="boundary",
                why=f"close for {s:g} s (persist {persist:g} s)",
            ),
            [(hit, s, 0)],
            fast,
        )
        for s in (below(persist, eps), above(persist, eps))
    ]
    return [
        *cases,
        scene.case(
            Spec(
                "interrupted_persist",
                f"close twice for {part:g} s with a break",
                "interruption",
                False,
            ),
            [(hit, part, 0), (scene.away, STEP_S, 0), (hit, part, 0)],
            fast,
        ),
        scene.case(
            _id_swap_spec(part, persist),
            [(hit, part, 0), (hit, part, 1)],
            fast,
        ),
    ]


# --- entry point --------------------------------------------------------------------------------


def _camera(rule: RuleIR) -> str:
    cameras = {spec.camera_id for spec in rule.zones.values()}
    if cameras:
        return sorted(cameras)[0]
    if rule.calibration and "@" in rule.calibration:
        return rule.calibration.rsplit("@", 1)[1]
    return DEFAULT_CAMERA


def shape_of(node: Predicate) -> str:
    """The predicate's operator tree, e.g. `persist(pose)`, for error messages."""
    inner = ",".join(shape_of(child) for _, child in children(node))
    return f"{node.op}({inner})" if inner else node.op


def generate_rule_traces(rule: RuleIR) -> list[TraceCase]:
    """Build the boundary traces for a rule from its own thresholds and zones.

    Args:
        rule: The rule.

    Returns:
        The traces, each with its expected outcome.

    Raises:
        UnsupportedRuleError: Rule CI has no trace generator for the rule's shape.
    """
    node: Predicate = rule.predicate
    persist_s = None
    if isinstance(rule.predicate, Persist):
        node, persist_s = rule.predicate.of, rule.predicate.seconds
    if isinstance(node, SequenceNode) and persist_s is None:
        return sequence_traces(rule, node)
    if isinstance(node, Inside):
        return inside_traces(rule, node, persist_s)
    parts = proximity_parts(node)
    if parts is not None:
        return proximity_traces(Proximity(_camera(rule), parts[0], parts[1], persist_s))
    msg = f"no boundary-trace generator for {shape_of(rule.predicate)}"
    raise UnsupportedRuleError(msg)
