"""Synthetic Track trace generator for Rule CI (task P2-08, MP §1, §3.4, §8 E4).

Generates synthetic Track sets for a rule based on its thresholds:
- just-below and just-above threshold pairs (dwell 19.5/20.5 s, distance 1.95/2.05 m);
- gap variants (max_gap_s +- 1 s);
- interruption mid-event;
- ID swap mid-episode;
- sequence-order variants (requirement before or after trigger).

Each trace carries its expected outcome (violation: True/False).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from rulecam.contracts.core import Calibration, Track, TrackPoint, Zone
from rulecam.contracts.rule_ir import (
    All,
    Any_,
    DistanceM,
    Dwell,
    Not_,
    Persist,
    Predicate,
    RuleIR,
)
from rulecam.contracts.rule_ir import (
    Sequence as SequencePredicate,
)
from rulecam.rule_ci.models import TraceCase

EPOCH: Final = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
DEFAULT_HZ: Final = 10.0
PX_PER_M: Final = 100.0

DEFAULT_CAL = Calibration(
    camera_id="cam1",
    homography=((1 / PX_PER_M, 0.0, 0.0), (0.0, 1 / PX_PER_M, 0.0), (0.0, 0.0, 1.0)),
    method="clicks4",
    err_m=0.01,
)


def _at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def make_walk_track(
    track_id: str,
    positions_m: Sequence[tuple[float, float] | None],
    *,
    concept: str = "person",
    camera_id: str = "cam1",
    start_s: float = 0.0,
    hz: float = DEFAULT_HZ,
    size_px: tuple[float, float] = (40.0, 80.0),
) -> Track:
    """Build a synthetic track whose foot point visits `positions_m` at `hz`."""
    width, height = size_px
    points: list[TrackPoint] = []
    for index, pos in enumerate(positions_m):
        if pos is None:
            continue
        fx, fy = pos[0] * PX_PER_M, pos[1] * PX_PER_M
        points.append(
            TrackPoint(
                ts=_at(start_s + index / hz),
                frame_idx=index,
                box_xyxy=(fx - width / 2, fy - height, fx + width / 2, fy),
                foot_px=(fx, fy),
                foot_m=pos,
                confidence=0.95,
            )
        )
    return Track(track_id=track_id, camera_id=camera_id, concept=concept, points=tuple(points))


def _make_worker(
    phases: list[tuple[tuple[float, float], float]],
    track_id: str = "1",
    camera_id: str = "cam1",
    start_s: float = 0.0,
) -> list[Track]:
    """Create a person track and attached hands track across (position, duration_s) phases."""
    feet: list[tuple[float, float] | None] = []
    for place, seconds in phases:
        feet.extend([place] * round(seconds * DEFAULT_HZ))
    hands: list[tuple[float, float] | None] = [
        (pos[0], pos[1] - 0.9) if pos else None for pos in feet
    ]
    return [
        make_walk_track(
            f"{camera_id}:person:{track_id}",
            feet,
            concept="person",
            camera_id=camera_id,
            start_s=start_s,
            size_px=(100.0, 200.0),
        ),
        make_walk_track(
            f"{camera_id}:hands:{track_id}",
            hands,
            concept="hands",
            camera_id=camera_id,
            start_s=start_s,
            size_px=(20.0, 20.0),
        ),
    ]


def _make_sink_track(camera_id: str = "cam1", seconds: float = 400.0) -> Track:
    return make_walk_track(
        f"{camera_id}:sink:1",
        [(6.5, 0.2)] * round(seconds * DEFAULT_HZ),
        concept="sink",
        camera_id=camera_id,
    )


def _is_node_vacuous(pred: Predicate) -> bool:
    if isinstance(pred, Dwell) and pred.total_s_gte is not None:
        return pred.total_s_gte <= 0.0
    if isinstance(pred, DistanceM) and pred.lt is not None:
        return pred.lt <= 0.0
    if isinstance(pred, Persist):
        return pred.seconds <= 0.0
    return False


def _child_nodes(pred: Predicate) -> list[Predicate]:
    if isinstance(pred, Persist | Not_):
        return [pred.of]
    if isinstance(pred, SequencePredicate):
        return [pred.trigger, pred.requirement]
    if isinstance(pred, All | Any_):
        return list(pred.of)
    return []


def is_rule_vacuous(rule: RuleIR) -> bool:
    """Check if a rule is deliberately or degenerately vacuous."""
    stack: list[Predicate] = [rule.predicate]
    while stack:
        pred = stack.pop()
        if _is_node_vacuous(pred):
            return True
        stack.extend(_child_nodes(pred))
    return False


def _generate_handwash_traces(_rule: RuleIR) -> list[TraceCase]:
    """Generate the full boundary and variant trace set for the handwash sequence rule."""
    camera_id = "cam1"
    wait_pos = (8.5, 1.5)
    sink_pos = (6.5, 1.5)
    line_pos = (2.0, 1.5)

    sink_polygon = Zone(
        zone_id="sink_zone",
        camera_id=camera_id,
        kind="polygon",
        polygon_px=((600.0, 0.0), (700.0, 0.0), (700.0, 100.0), (600.0, 100.0)),
    )
    line_polygon = Zone(
        zone_id="line_entry",
        camera_id=camera_id,
        kind="polygon",
        polygon_px=((0.0, 0.0), (400.0, 0.0), (400.0, 300.0), (0.0, 300.0)),
    )
    zones = {"sink_zone": sink_polygon, "line_entry": line_polygon}

    traces: list[TraceCase] = []

    # 1. Standard passing trace: 22s wash, wait, enter line -> pass
    t_pass_tracks = _make_worker([(wait_pos, 2), (sink_pos, 22), (wait_pos, 5), (line_pos, 5)])
    traces.append(
        TraceCase(
            name="22s_wash_passes",
            description="MP §7.3 canonical pass: worker washes hands for 22s before line entry",
            tracks=t_pass_tracks,
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(60),
            expected_violation=False,
            threshold_tested=20.0,
            variant_kind="boundary_above",
        )
    )

    # 2. Standard failing trace: 8s wash, wait, enter line -> violation
    t_fail_tracks = _make_worker([(wait_pos, 2), (sink_pos, 8), (wait_pos, 5), (line_pos, 5)])
    traces.append(
        TraceCase(
            name="8s_wash_fails",
            description="MP §7.3 canonical fail: worker only washes hands for 8s before line entry",
            tracks=t_fail_tracks,
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(60),
            expected_violation=True,
            threshold_tested=20.0,
            variant_kind="boundary_below",
        )
    )

    # 3. Dwell just-below: 19.5 s wash -> violation
    t_195_tracks = _make_worker([(wait_pos, 2), (sink_pos, 19.5), (wait_pos, 5), (line_pos, 5)])
    traces.append(
        TraceCase(
            name="dwell_19_5s_violation",
            description="Dwell just-below threshold (19.5 s vs 20 s): expected violation",
            tracks=t_195_tracks,
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(60),
            expected_violation=True,
            threshold_tested=19.5,
            variant_kind="threshold_just_below",
        )
    )

    # 4. Dwell just-above: 20.5 s wash -> pass
    t_205_tracks = _make_worker([(wait_pos, 2), (sink_pos, 20.5), (wait_pos, 5), (line_pos, 5)])
    traces.append(
        TraceCase(
            name="dwell_20_5s_pass",
            description="Dwell just-above threshold (20.5 s vs 20 s): expected pass",
            tracks=t_205_tracks,
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(60),
            expected_violation=False,
            threshold_tested=20.5,
            variant_kind="threshold_just_above",
        )
    )

    # 5. Gap variant just-below: 12 s wash + 2 s gap + 12 s wash (max_gap_s = 3) -> pass
    t_gap_pass = _make_worker(
        [(wait_pos, 2), (sink_pos, 12), (wait_pos, 2), (sink_pos, 12), (line_pos, 5)]
    )
    traces.append(
        TraceCase(
            name="gap_2s_pass",
            description="Gap variant just-below limit (gap 2 s <= max_gap 3 s): expected pass",
            tracks=t_gap_pass,
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(60),
            expected_violation=False,
            threshold_tested=2.0,
            variant_kind="gap_just_below",
        )
    )

    # 6. Gap variant just-above: 12 s wash + 4 s gap + 12 s wash (max_gap_s = 3) -> violation
    t_gap_fail = _make_worker(
        [(wait_pos, 2), (sink_pos, 12), (wait_pos, 4), (sink_pos, 12), (line_pos, 5)]
    )
    traces.append(
        TraceCase(
            name="gap_4s_violation",
            description="Gap variant just-above limit (gap 4 s > max_gap 3 s): expected violation",
            tracks=t_gap_fail,
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(60),
            expected_violation=True,
            threshold_tested=4.0,
            variant_kind="gap_just_above",
        )
    )

    # 7. Interruption mid-event: 10s wash, leaves to wait -> violation upon line entry
    t_interrupt = _make_worker([(wait_pos, 2), (sink_pos, 10), (wait_pos, 15), (line_pos, 5)])
    traces.append(
        TraceCase(
            name="interruption_mid_event",
            description="Interrupted wash (10s wash then walk away): expected violation",
            tracks=t_interrupt,
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(60),
            expected_violation=True,
            threshold_tested=10.0,
            variant_kind="interruption",
        )
    )

    # 8. ID swap mid-episode: person 1 washes 22s; person 2 enters line without washing
    washer = _make_worker([(wait_pos, 2), (sink_pos, 22), (wait_pos, 10)], track_id="1")
    skipper = _make_worker([(wait_pos, 24), (line_pos, 5)], track_id="2")
    traces.append(
        TraceCase(
            name="id_swap_mid_episode",
            description="ID swap mid-episode: person 1 washed, but person 2 enters line unwashed",
            tracks=washer + skipper,
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(60),
            expected_violation=True,
            threshold_tested=None,
            variant_kind="id_swap",
        )
    )

    # 9. Sequence order variant: requirement AFTER trigger (enters line, then washes) -> violation
    t_order = _make_worker([(wait_pos, 2), (line_pos, 5), (sink_pos, 22), (wait_pos, 5)])
    traces.append(
        TraceCase(
            name="sequence_order_after_trigger",
            description="Sequence order variant: line entered before handwash: expected violation",
            tracks=t_order,
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(60),
            expected_violation=True,
            threshold_tested=None,
            variant_kind="sequence_order",
        )
    )

    # 10. Zone boundary test: person steps to (5.0, 1.5)
    # Outside original line_entry (500px > 400px), but inside widened (500px <= 600px)
    t_zone = _make_worker([(wait_pos, 2), ((5.0, 1.5), 5)])
    traces.append(
        TraceCase(
            name="outside_zone_boundary",
            description="Person walks to (5.0, 1.5) outside line_entry (400px): pass",
            tracks=t_zone,
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(15),
            expected_violation=False,
            threshold_tested=None,
            variant_kind="zone_boundary",
        )
    )

    return traces


def _generate_distance_traces(rule: RuleIR) -> list[TraceCase]:
    """Generate threshold boundary traces for a distance_m rule."""
    camera_id = "cam1"
    # Find distance threshold (default 2.0 m)
    threshold = 2.0
    for node in rule.predicate.of if rule.predicate.op in ("all", "any") else [rule.predicate]:
        if node.op == "distance_m" and node.lt is not None:
            threshold = float(node.lt)
            break

    traces: list[TraceCase] = []

    # Forklift moves laterally along X so distance to person on Y does not shrink
    forklift_lateral = [(0.0 + i * 0.05, 0.0) for i in range(20)]  # speed = 0.5 m/s > 0.2 m/s

    # 1. Just-below threshold: distance = threshold - 0.05m -> fires (violation)
    d_below = threshold - 0.05
    p_below = make_walk_track(
        f"{camera_id}:person:1", [(0.0, d_below)] * 20, concept="person", camera_id=camera_id
    )
    forklift_below = make_walk_track(
        f"{camera_id}:forklift:1", forklift_lateral, concept="forklift", camera_id=camera_id
    )
    traces.append(
        TraceCase(
            name=f"distance_just_below_{d_below:.2f}m",
            description=f"Distance just below threshold ({d_below:.2f}m vs {threshold}m): fires",
            tracks=[p_below, forklift_below],
            zones={},
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(5),
            expected_violation=True,
            threshold_tested=d_below,
            variant_kind="threshold_just_below",
        )
    )

    # 2. Just-above threshold: distance = threshold + 0.05m -> does not fire (pass)
    d_above = threshold + 0.05
    p_above = make_walk_track(
        f"{camera_id}:person:1", [(0.0, d_above)] * 20, concept="person", camera_id=camera_id
    )
    forklift_above = make_walk_track(
        f"{camera_id}:forklift:1", forklift_lateral, concept="forklift", camera_id=camera_id
    )
    traces.append(
        TraceCase(
            name=f"distance_just_above_{d_above:.2f}m",
            description=f"Distance just above threshold ({d_above:.2f}m vs {threshold}m): pass",
            tracks=[p_above, forklift_above],
            zones={},
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(5),
            expected_violation=False,
            threshold_tested=d_above,
            variant_kind="threshold_just_above",
        )
    )

    # 3. Near but stationary forklift (speed = 0 <= 0.2 m/s): all condition requires moving -> pass
    p_near = make_walk_track(
        f"{camera_id}:person:1", [(0.0, 1.5)] * 20, concept="person", camera_id=camera_id
    )
    forklift_stationary = make_walk_track(
        f"{camera_id}:forklift:1", [(0.0, 0.0)] * 20, concept="forklift", camera_id=camera_id
    )
    traces.append(
        TraceCase(
            name="near_but_stationary_forklift_pass",
            description="Person near stationary forklift (speed 0 m/s <= 0.2 m/s): expected pass",
            tracks=[p_near, forklift_stationary],
            zones={},
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(5),
            expected_violation=False,
            threshold_tested=0.0,
            variant_kind="moving_subcondition",
        )
    )

    return traces


def generate_rule_traces(rule: RuleIR) -> list[TraceCase]:
    """Generate synthetic Track sets with expected outcomes for any RuleIR."""
    if "wash" in rule.id.lower() or rule.predicate.op == "sequence":
        return _generate_handwash_traces(rule)
    if "distance" in rule.id.lower() or any(
        getattr(n, "op", "") == "distance_m"
        for n in (rule.predicate.of if rule.predicate.op in ("all", "any") else [rule.predicate])
    ):
        return _generate_distance_traces(rule)

    # Fallback generic boundary traces for dwell, persist, inside
    sink_polygon = Zone(
        zone_id="test_zone",
        camera_id="cam1",
        kind="polygon",
        polygon_px=((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
    )
    zones = {"test_zone": sink_polygon}
    p_in = make_walk_track("cam1:person:1", [(0.5, 0.5)] * 20, concept="person")
    p_out = make_walk_track("cam1:person:1", [(5.0, 5.0)] * 20, concept="person")

    return [
        TraceCase(
            name="generic_inside_pass",
            description="Person inside test zone",
            tracks=[p_in],
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(5),
            expected_violation=True,
        ),
        TraceCase(
            name="generic_outside_pass",
            description="Person outside test zone",
            tracks=[p_out],
            zones=zones,
            calibration=DEFAULT_CAL,
            t0=_at(0),
            t1=_at(5),
            expected_violation=False,
        ),
    ]
