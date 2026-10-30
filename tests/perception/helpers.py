"""Builders shared by the perception tests."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from rulecam.contracts import RuleIR, Severity
from rulecam.contracts.rule_ir import ActionSpec, ConceptSpec, Inside, RuleSource


def make_rule(
    rule_id: str = "rule",
    *,
    dynamic: tuple[str, ...] = ("person",),
    static: tuple[str, ...] = (),
    severity: Severity = Severity.MEDIUM,
) -> RuleIR:
    """A minimal valid rule using the given dynamic and static concepts."""
    concepts = {name: ConceptSpec(prompt=name) for name in dynamic}
    concepts |= {name: ConceptSpec(prompt=name, static=True) for name in static}
    return RuleIR(
        id=rule_id,
        source=RuleSource(kind="text", text="test rule"),
        concepts=concepts,
        predicate=Inside(object=(dynamic or static)[0], zone="zone"),
        severity=severity,
        actions=(ActionSpec(kind="log"),),
    )


def still_frames(
    n: int = 30, *, shape: tuple[int, int] = (90, 160), level: int = 90
) -> list[NDArray[np.uint8]]:
    """Identical grayscale frames."""
    return [np.full(shape, level, dtype=np.uint8) for _ in range(n)]


def moving_square_frames(
    n: int = 30, *, shape: tuple[int, int] = (90, 160), side: int = 8, step: int = 3
) -> list[NDArray[np.uint8]]:
    """A bright square sliding right across a flat background."""
    frames = []
    for i in range(n):
        frame = np.full(shape, 90, dtype=np.uint8)
        x = (i * step) % (shape[1] - side)
        frame[40 : 40 + side, x : x + side] = 250
        frames.append(frame)
    return frames
