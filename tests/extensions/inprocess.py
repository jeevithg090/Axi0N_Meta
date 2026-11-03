"""An in-process stand-in for `extensions.sandbox.run_check`, for unit tests only.

It runs our own trusted test checks with the same calling convention and outcome shape as the
sandbox (`CheckOutcome.result.value / .why`, `.error`), records every batch size, and never
spawns a child process. Production code always gets the real sandbox runner.
"""

from __future__ import annotations

import builtins
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

_SAFE = (
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "isinstance", "len",
    "list", "max", "min", "range", "round", "sorted", "str", "sum", "tuple", "zip",
)  # fmt: skip


@dataclass(frozen=True)
class FakeResult:
    value: bool | None
    why: str = ""


@dataclass(frozen=True)
class FakeOutcome:
    result: FakeResult | None
    error: str | None = None


def _import(name: str, *_args: object, **_kwargs: object) -> object:
    if name != "math":
        raise ImportError(name)
    return math


@dataclass
class InProcessRunner:
    """Calls `check(sample)` in this process; records each batch's size in `batches`."""

    batches: list[int] = field(default_factory=list)

    def __call__(self, code: str, samples: Sequence[Mapping[str, object]], /) -> list[FakeOutcome]:
        self.batches.append(len(samples))
        scope: dict[str, object] = {
            "__builtins__": {name: getattr(builtins, name) for name in _SAFE}
            | {"__import__": _import, "ImportError": ImportError}
        }
        exec(compile(code, "<test-check>", "exec"), scope)  # noqa: S102 - trusted test checks only
        check = scope["check"]
        assert callable(check)
        return [self._one(check, dict(sample)) for sample in samples]

    @staticmethod
    def _one(check: object, sample: dict[str, object]) -> FakeOutcome:
        assert callable(check)
        try:
            answer = check(sample)
        except Exception as exc:  # every failure becomes an outcome, as in the sandbox
            return FakeOutcome(result=None, error=f"{type(exc).__name__}: {exc}")
        value = answer.get("value")
        if value is not None and not isinstance(value, bool):
            return FakeOutcome(result=None, error="value is not a bool")
        return FakeOutcome(result=FakeResult(value=value, why=str(answer.get("why", ""))))
