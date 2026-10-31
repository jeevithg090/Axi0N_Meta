"""Pick the boundary-trace generator for a rule's shape (task P2-08, MP §3.4).

| Shape | Generator |
|---|---|
| `sequence(inside -> dwell)` | `traces.sequence_traces` |
| `[persist] inside(x, buffer of y)`, alone or with `moving(x or y)` under `all` | `buffer_traces` |
| `[persist] inside(x, polygon)` | `traces.inside_traces` |
| `[persist] distance_m`, alone or with `moving` under `all` | `traces.proximity_traces` |

For buffer rules and `distance_m(lt=...)` rules, `kinematic_traces` adds the approach, pass-by
and co-moving-far traces the near-miss (TTC) metrics describe, and, for person-vehicle rules,
the operator-vs-pedestrian traces.

Anything else raises `UnsupportedRuleError` naming the shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from rulecam.contracts.core import Calibration
from rulecam.contracts.rule_ir import Inside, Persist, Predicate, RuleIR
from rulecam.contracts.rule_ir import Sequence as SequenceNode
from rulecam.rule_ci.buffer_traces import buffer_shape, buffer_traces, ground_plane
from rulecam.rule_ci.geometry import UnsupportedRuleError
from rulecam.rule_ci.kinematic_traces import (
    Separation,
    from_buffer,
    from_proximity,
    kinematic_traces,
    operator_traces,
)
from rulecam.rule_ci.models import TraceCase
from rulecam.rule_ci.traces import (
    Proximity,
    inside_traces,
    proximity_parts,
    proximity_traces,
    sequence_traces,
)
from rulecam.rules.ir import children

DEFAULT_CAMERA: Final = "cam1"


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


def generate_rule_traces(
    rule: RuleIR, *, calibrations: Mapping[str, Calibration] | None = None
) -> list[TraceCase]:
    """Build the boundary traces for a rule from its own thresholds and zones.

    Args:
        rule: The rule.
        calibrations: Real ground calibrations by camera. A buffer-zone rule's traces are laid
            out through its camera's homography from here; without one, the synthetic ground
            plane stands in when the rule names `ground-plane@<camera>`.

    Returns:
        The traces, each with its expected outcome.

    Raises:
        UnsupportedRuleError: Rule CI has no trace generator for the rule's shape, or a buffer
            zone has no calibration to measure metres with.
    """
    node: Predicate = rule.predicate
    persist_s = None
    if isinstance(rule.predicate, Persist):
        node, persist_s = rule.predicate.of, rule.predicate.seconds
    if isinstance(node, SequenceNode) and persist_s is None:
        return sequence_traces(rule, node)
    buffer = buffer_shape(rule, node, persist_s)
    if buffer is not None:
        cases = buffer_traces(buffer, ground_plane(rule, buffer, calibrations or {}))
        return cases + _separation_traces(from_buffer(rule, buffer))
    if isinstance(node, Inside):
        return inside_traces(rule, node, persist_s)
    parts = proximity_parts(node)
    if parts is not None:
        scene = Proximity(_camera(rule), parts[0], parts[1], persist_s)
        return proximity_traces(scene) + _separation_traces(from_proximity(rule, scene))
    msg = f"no boundary-trace generator for {shape_of(rule.predicate)}"
    raise UnsupportedRuleError(msg)


def _separation_traces(separation: Separation | None) -> list[TraceCase]:
    if separation is None:
        return []
    return kinematic_traces(separation) + operator_traces(separation)
