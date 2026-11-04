"""Helpers for walking a Rule IR predicate tree."""

from __future__ import annotations

from collections.abc import Iterator

from rulecam.contracts.rule_ir import (
    Absence,
    All,
    Any_,
    Count,
    DistanceM,
    Dwell,
    Inside,
    Moving,
    Not_,
    Overlap,
    Persist,
    PoseState,
    Predicate,
    Rate,
    Sequence,
    StateCheck,
    Without,
)
from rulecam.rules.extension import EXTENSION_PREFIX

REF_PARTS = frozenset({"foot_point", "mask", "hands"})
HANDS_CONCEPT = "hands"


def parse_ref(ref: str) -> tuple[str, str | None]:
    """Split an object reference into its concept and optional part.

    Args:
        ref: `"<concept>"` or `"<concept>.foot_point" | ".mask" | ".hands"`.

    Returns:
        `(concept, part)`; `part` is None for a bare concept.
    """
    base, sep, part = ref.rpartition(".")
    if sep and base and part in REF_PARTS:
        return base, part
    return ref, None


def children(node: Predicate) -> list[tuple[str, Predicate]]:
    """Direct sub-predicates of a node, each with its label in trace paths."""
    if isinstance(node, All | Any_):
        return [(str(index), child) for index, child in enumerate(node.of)]
    if isinstance(node, Not_ | Persist | Rate):
        return [("", node.of)]
    if isinstance(node, Sequence):
        return [("trigger", node.trigger), ("requirement", node.requirement)]
    return []


def child_path(parent: str, label: str, child: Predicate) -> str:
    """Trace path of a child, e.g. `sequence.requirement.dwell` or `persist.all.0.distance_m`."""
    return ".".join(part for part in (parent, label, child.op) if part)


def walk(node: Predicate, path: str | None = None) -> Iterator[tuple[str, Predicate]]:
    """Yield `(path, node)` for every node in pre-order; the root's path is its op."""
    here = path if path is not None else node.op
    yield here, node
    for label, child in children(node):
        yield from walk(child, child_path(here, label, child))


def state_concepts(node: StateCheck) -> tuple[str, ...]:
    """Concept names that mean `node.object` is in `node.state` (e.g. "open door").

    An extension state (`ext:…`) reads the object itself; the extension decides the state.
    """
    if node.state == "present" or node.state.startswith(EXTENSION_PREFIX):
        return (node.object,)
    return (f"{node.state} {node.object}", f"{node.object}/{node.state}")


def referenced_concepts(node: Predicate) -> set[str]:
    """Every concept a single node reads tracks of (not including its children)."""
    refs: tuple[str, ...]
    if isinstance(node, DistanceM | Overlap):
        refs = (node.a, node.b)
    elif isinstance(node, Without):
        refs = (node.subject, node.item)
    elif isinstance(node, Inside | Moving | Count | PoseState | Dwell | Absence):
        refs = (node.object,)
    elif isinstance(node, StateCheck):
        return set(state_concepts(node))
    else:
        refs = ()
    out = set()
    for ref in refs:
        concept, part = parse_ref(ref)
        out.add(concept)
        if part == HANDS_CONCEPT:
            out.add(HANDS_CONCEPT)
    return out


def referenced_zones(node: Predicate) -> set[str]:
    """Names of zones a single node reads (not including its children)."""
    if isinstance(node, Inside | Dwell | Absence):
        return {node.zone}
    if isinstance(node, Count) and node.zone is not None:
        return {node.zone}
    return set()
