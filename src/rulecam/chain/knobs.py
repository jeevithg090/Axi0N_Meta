"""Read and set a template's tunable thresholds (knobs) inside its predicate.

A knob's `path` walks the predicate's JSON form: string keys into objects, integer indices into
lists (`("of", 0, "lt")` is the `lt` of the first child of an `all`). Setting a knob rebuilds the
rule through validation, so a value the predicate cannot hold (5.5 people) is an error, not a
silently rounded number.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from rulecam.chain.errors import ChainError
from rulecam.chain.models import Knob, KnobPath
from rulecam.contracts.rule_ir import RuleIR


class KnobError(ChainError):
    """A knob's path does not lead to a number, or its value does not fit the predicate."""


def _parent(data: Any, path: KnobPath) -> tuple[Any, str | int]:
    node = data
    for key in path[:-1]:
        node = _step(node, key, path)
    last = path[-1]
    _step(node, last, path)
    return node, last


def _step(node: Any, key: str | int, path: KnobPath) -> Any:
    if isinstance(node, dict) and isinstance(key, str) and key in node:
        return node[key]
    if isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
        return node[key]
    msg = f"path {list(path)} does not exist in the predicate (stuck at {key!r})"
    raise KnobError(msg)


def knob_value(rule: RuleIR, knob: Knob) -> float:
    """The number a knob's path points at in the rule's predicate.

    Args:
        rule: The rule.
        knob: The knob.

    Returns:
        The value.

    Raises:
        KnobError: The path does not exist or does not end on a number.
    """
    data = rule.predicate.model_dump(mode="json")
    parent, key = _parent(data, knob.path)
    value = parent[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"knob {knob.name!r}: path {list(knob.path)} holds {value!r}, not a number"
        raise KnobError(msg)
    return float(value)


def with_knobs(rule: RuleIR, knobs: Sequence[Knob], values: Mapping[str, float]) -> RuleIR:
    """The rule with some knobs set.

    Args:
        rule: The rule.
        knobs: The template's knobs.
        values: Knob name -> value; knobs not named keep the rule's value.

    Returns:
        A new, validated rule.

    Raises:
        KnobError: A path does not exist, or a value does not fit the predicate.
    """
    data = rule.model_dump(mode="json")
    for knob in knobs:
        if knob.name not in values:
            continue
        parent, key = _parent(data["predicate"], knob.path)
        parent[key] = values[knob.name]
    try:
        return RuleIR.model_validate(data)
    except ValidationError as exc:
        msg = f"knob values {dict(values)} do not fit rule {rule.id!r}: {exc.errors()[0]['msg']}"
        raise KnobError(msg) from exc


def knob_defaults(rule: RuleIR, knobs: Sequence[Knob]) -> dict[str, float]:
    """Knob name -> the value HQ wrote into the template."""
    return {knob.name: knob_value(rule, knob) for knob in knobs}
