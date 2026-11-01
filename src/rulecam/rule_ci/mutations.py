"""Mutation operators on a Rule IR (task P2-08, MP §3.4).

The operators are the task book's list: scale every threshold by x0.1 and by x10, flip `lt`/`gt`,
drop a `not`, swap `all`/`any`, remove `persist`, switch a sequence's `semantics` between past and
future, and widen each zone by 50% (a polygon about its centroid, a buffer's radius). A rule that
relates a person and a vehicle also gets `drop_operator_exclusion`: the same rule evaluated with
the engine's operator exclusion off (WS1c), which the riding-operator trace must catch, and
`drop_operator_warmup`: the same rule with the operator warm-up off (P1-07), which the trace of a
rider first seen on board through a re-identification (`operator_born_riding`) must catch (not
made under a `persist` at least as long as the warm-up: the hold is shorter than the persistence,
so the two rules always agree). An
operator that has nothing to change in a rule makes no mutant. A mutant is killed when at least one
boundary trace's outcome changes (`rule_ci.runner`).
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from typing import Any, Final

from rulecam.contracts.rule_ir import Persist, RuleIR, ZoneSpec
from rulecam.rule_ci.models import MutationCase
from rulecam.rules.ir import walk
from rulecam.rules.operators import OPERATOR_WARMUP_S, has_operator_pair

THRESHOLD_KEYS: Final = (
    "lt",
    "gt",
    "total_s_gte",
    "max_gap_s",
    "seconds",
    "speed_mps_gt",
    "per_minute_gt",
    "require_within_s",
)
WIDEN_FACTOR: Final = 1.5
OPERATORS: Final = (
    "scale_threshold_0.1",
    "scale_threshold_10",
    "flip_lt_gt",
    "drop_not",
    "swap_all_any",
    "remove_persist",
    "switch_semantics",
    "widen_zone",
    "drop_operator_exclusion",
    "drop_operator_warmup",
)

Node = dict[str, Any]
Edit = Callable[[Node], Node | None]
"""Returns a replacement for a predicate node, or `None` to leave it unchanged."""


def _kids(node: Node) -> Iterator[tuple[str, int | None]]:
    for key in ("of", "trigger", "requirement"):
        value = node.get(key)
        if isinstance(value, dict):
            yield key, None
        elif isinstance(value, list):
            yield from ((key, index) for index in range(len(value)))


def rewrite(node: Node, edit: Edit) -> tuple[Node, bool]:
    """Apply `edit` to every node of a predicate tree (top-down) and say whether anything changed.

    Args:
        node: A predicate as JSON (`model_dump(mode="json")`); it is not modified.
        edit: The node edit.

    Returns:
        The new tree and whether `edit` changed any node.
    """
    replaced = edit(node)
    changed = replaced is not None
    out = copy.deepcopy(replaced if replaced is not None else node)
    for key, index in _kids(out):
        child = out[key] if index is None else out[key][index]
        new, child_changed = rewrite(child, edit)
        changed |= child_changed
        if index is None:
            out[key] = new
        else:
            out[key][index] = new
    return out, changed


def scale_thresholds(factor: float) -> Edit:
    """An edit multiplying every numeric threshold by `factor` (counts stay whole numbers)."""

    def edit(node: Node) -> Node | None:
        out = dict(node)
        for key in THRESHOLD_KEYS:
            value = node.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                scaled = value * factor
                out[key] = round(scaled) if node["op"] == "count" else round(scaled, 6)
        return out if out != node else None

    return edit


def flip_lt_gt(node: Node) -> Node | None:
    """Swap the `lt` and `gt` bounds of a `distance_m` or `count` node."""
    if node["op"] not in {"distance_m", "count"} or (node.get("lt"), node.get("gt")) == (
        None,
        None,
    ):
        return None
    return {**node, "lt": node.get("gt"), "gt": node.get("lt")}


def drop_not(node: Node) -> Node | None:
    """Replace a `not` node by its operand."""
    return dict(node["of"]) if node["op"] == "not" else None


def swap_all_any(node: Node) -> Node | None:
    """Turn `all` into `any` and back."""
    swapped = {"all": "any", "any": "all"}.get(node["op"])
    return {**node, "op": swapped} if swapped else None


def remove_persist(node: Node) -> Node | None:
    """Replace a `persist` node by its operand."""
    return dict(node["of"]) if node["op"] == "persist" else None


def switch_semantics(node: Node) -> Node | None:
    """Switch a `sequence` between past and future semantics."""
    if node["op"] != "sequence":
        return None
    return {**node, "semantics": "future" if node["semantics"] == "past" else "past"}


def widen_zone(spec: ZoneSpec) -> ZoneSpec | None:
    """The zone widened by 50%: a polygon scaled about its vertex centroid, or a larger buffer."""
    if spec.kind == "buffer" and spec.buffer_m is not None:
        return spec.model_copy(update={"buffer_m": spec.buffer_m * WIDEN_FACTOR})
    if not spec.polygon_px:
        return None
    cx = sum(x for x, _ in spec.polygon_px) / len(spec.polygon_px)
    cy = sum(y for _, y in spec.polygon_px) / len(spec.polygon_px)
    polygon = tuple(
        (cx + (x - cx) * WIDEN_FACTOR, cy + (y - cy) * WIDEN_FACTOR) for x, y in spec.polygon_px
    )
    return spec.model_copy(update={"polygon_px": polygon})


_PREDICATE_OPERATORS: Final[tuple[tuple[str, Edit, str], ...]] = (
    ("scale_threshold_0.1", scale_thresholds(0.1), "every threshold x0.1"),
    ("scale_threshold_10", scale_thresholds(10.0), "every threshold x10"),
    ("flip_lt_gt", flip_lt_gt, "lt and gt swapped"),
    ("drop_not", drop_not, "not removed"),
    ("swap_all_any", swap_all_any, "all and any swapped"),
    ("remove_persist", remove_persist, "persist removed"),
    ("switch_semantics", switch_semantics, "sequence semantics past <-> future"),
)


def generate_mutations(rule: RuleIR) -> list[MutationCase]:
    """Every applicable mutant of a rule, in `OPERATORS` order (one `widen_zone` per zone).

    Args:
        rule: The rule.

    Returns:
        The mutants.
    """
    data = rule.model_dump(mode="json")
    mutants: list[MutationCase] = []
    for operator, edit, description in _PREDICATE_OPERATORS:
        predicate, changed = rewrite(data["predicate"], edit)
        if changed:
            mutated = RuleIR.model_validate({**data, "predicate": predicate})
            mutants.append(
                MutationCase(
                    name=operator, operator=operator, rule=mutated, description=description
                )
            )
    for name, spec in sorted(rule.zones.items()):
        wider = widen_zone(spec)
        if wider is None:
            continue
        mutated = rule.model_copy(update={"zones": {**rule.zones, name: wider}})
        mutants.append(
            MutationCase(
                name=f"widen_zone:{name}",
                operator="widen_zone",
                rule=RuleIR.model_validate(mutated.model_dump()),
                description=f"zone {name!r} widened by 50%",
            )
        )
    if has_operator_pair(rule):
        mutants.append(
            MutationCase(
                name="drop_operator_exclusion",
                operator="drop_operator_exclusion",
                rule=rule,
                description="operator exclusion off: the vehicle's rider counts as a pedestrian",
                operator_exclusion=False,
            )
        )
    if has_operator_pair(rule) and _longest_persist(rule) < OPERATOR_WARMUP_S:
        mutants.append(
            MutationCase(
                name="drop_operator_warmup",
                operator="drop_operator_warmup",
                rule=rule,
                description="operator warm-up off: a rider first seen on board is judged at once",
                operator_warmup_s=0.0,
            )
        )
    return mutants


def _longest_persist(rule: RuleIR) -> float:
    """The longest `persist` in the rule's predicate, in seconds (0 without one)."""
    return max((n.seconds for _, n in walk(rule.predicate) if isinstance(n, Persist)), default=0.0)
