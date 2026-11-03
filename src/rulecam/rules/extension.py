"""The engine's hook for model-written state checks (`rulecam.extensions`).

A rule refers to an approved extension through the existing `StateCheck` predicate, with the
state `ext:<name>@<sha12>` (no contract change). The engine does not run model-written code
itself: `evaluate(..., extensions=hook)` passes an `ExtensionHook`, and the hook decides whether
that exact version is approved and runs it in the sandbox. Without a hook, or when the hook
returns None (unknown, unapproved or failed extension), every sample is *unknown*: the predicate
is false and the trace says `unknown`, so the rule can never read as compliant because of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

import numpy as np

from rulecam.rules.geometry import BoolArray, FloatArray

if TYPE_CHECKING:
    from rulecam.contracts.rule_ir import StateCheck
    from rulecam.rules.scene import Scene

EXTENSION_PREFIX: Final = "ext:"
_REF = re.compile(r"^ext:(?P<name>[a-z][a-z0-9_]{2,47})@(?P<sha>[0-9a-f]{12})$")


def is_extension_state(state: str) -> bool:
    """Whether a `StateCheck` state names an extension (`ext:…`)."""
    return state.startswith(EXTENSION_PREFIX)


def parse_extension_ref(state: str) -> tuple[str, str] | None:
    """`(name, sha12)` of an `ext:<name>@<sha12>` state, or None if it is malformed."""
    found = _REF.match(state)
    return None if found is None else (found["name"], found["sha"])


def extension_ref(name: str, sha256: str) -> str:
    """The `StateCheck.state` that names one version of an extension."""
    return f"{EXTENSION_PREFIX}{name}@{sha256[:12]}"


@dataclass(frozen=True, eq=False)
class ExtensionValues:
    """An extension's answers over a scene's timeline.

    Attributes:
        truth: Whether the check holds at each sample.
        known: Whether the check gave a valid answer at each sample.
    """

    truth: BoolArray
    known: BoolArray


class ExtensionHook(Protocol):
    """Runs an approved extension over a scene (implemented by `extensions.registry`)."""

    def evaluate(self, node: StateCheck, scene: Scene) -> ExtensionValues | None:
        """The extension's answers, or None when it must not or could not run."""
        ...


def unknown_values(n: int) -> tuple[BoolArray, FloatArray, BoolArray]:
    """Truth, value and known arrays for a timeline where nothing is known."""
    return np.zeros(n, dtype=np.bool_), np.full(n, np.nan), np.zeros(n, dtype=np.bool_)
