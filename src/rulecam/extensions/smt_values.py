"""Symbolic Python values for the SMT proof of a model-written check (`extensions.smt`).

The symbolic executor (`smt_exec`) runs a check's Python over values whose numbers and truth
values are Z3 terms. Each value class mirrors one Python type the check can meet in a sample:

- `SNum`: an int or float, as a Z3 Real term (exact real arithmetic; float rounding is ignored);
- `SBool`: a bool, as a Z3 Bool term;
- `SStr`: a string, known (`text`) or opaque (`None`: only its type is known, e.g. an f-string);
- `SConcept`: an object's concept name, an Int code into a finite tuple of names;
- `SNone`, `SMaybe` (a value that may be None, e.g. `ground_xy_m`), `SList`, `SDict`;
- `SCallable` (a builtin, a `math` function, a bound method or a module-level function) and
  `SModule` (`math`).

Lists and dicts always have a concrete length and concrete string keys: the executor enumerates
the number of objects, so nothing about a list's shape is symbolic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from rulecam.errors import RulecamError

# "num": an int or a float depending on the inputs (e.g. max(1, 2.5)); isinstance refuses it.
NumKind = Literal["int", "float", "num"]
SeqKind = Literal["list", "tuple"]


class UnsupportedError(RulecamError):
    """The check uses a construct the prover cannot encode exactly (so nothing is proved)."""

    def __init__(self, construct: str, line: int) -> None:
        """Name the construct and its line.

        Args:
            construct: What is unsupported, in plain words.
            line: The source line (0 when unknown).
        """
        self.construct = construct
        self.line = line
        super().__init__(f"line {line}: {construct}")


class PathLimitError(RulecamError):
    """The check branches into more paths than the prover's budget allows."""


@dataclass(frozen=True)
class SNum:
    """A number: a Z3 Real term and whether Python would hold it as an int or a float."""

    expr: Any
    kind: NumKind = "float"


@dataclass(frozen=True)
class SBool:
    """A bool, as a Z3 Bool term."""

    expr: Any


@dataclass(frozen=True)
class SStr:
    """A string; `text` is None when only its type is known."""

    text: str | None


@dataclass(frozen=True)
class SConcept:
    """An object's concept: `code` (a Z3 Int term) indexes `names`."""

    code: Any
    names: tuple[str, ...]


@dataclass(frozen=True)
class SNone:
    """Python's None."""


@dataclass(frozen=True)
class SMaybe:
    """A value that is None when `is_none` (a Z3 Bool term) holds, else `value`."""

    is_none: Any
    value: Value


@dataclass(frozen=True)
class SList:
    """A list or tuple of known length."""

    items: tuple[Value, ...]
    kind: SeqKind = "list"


@dataclass(frozen=True)
class SDict:
    """A dict with known string keys, in insertion order."""

    entries: tuple[tuple[str, Value], ...]

    def lookup(self, key: str) -> Value | None:
        """The value under `key`, or None when the key is missing.

        Args:
            key: The key.

        Returns:
            The value, or None.
        """
        for name, value in self.entries:
            if name == key:
                return value
        return None

    def with_entry(self, key: str, value: Value) -> SDict:
        """A copy with `key` set to `value` (replacing or appending).

        Args:
            key: The key.
            value: The new value.

        Returns:
            The new dict.
        """
        kept = tuple((k, v) for k, v in self.entries if k != key)
        if len(kept) == len(self.entries):
            return SDict((*self.entries, (key, value)))
        return SDict(tuple((k, value if k == key else v) for k, v in self.entries))


@dataclass(frozen=True)
class SCallable:
    """Something callable: a builtin or type (`len`, `int`), `math.<f>`, a method or a function.

    Attributes:
        name: `len`, `math.sqrt`, `method.append`, or `user.<function name>`.
        receiver: The bound receiver of a method.
        receiver_var: The variable holding the receiver, when it is a plain name (for
            `list.append`, which rebinds that variable).
    """

    name: str
    receiver: Value | None = None
    receiver_var: str | None = None


@dataclass(frozen=True)
class SModule:
    """An imported module (only `math`)."""

    name: str


Value = SNum | SBool | SStr | SConcept | SNone | SMaybe | SList | SDict | SCallable | SModule


@dataclass(frozen=True)
class Failure:
    """A path on which the check raises, or returns something that is not a valid result.

    Attributes:
        kind: The Python exception's name, or `InvalidResult`.
        detail: What went wrong, in plain words.
        line: The source line.
    """

    kind: str
    detail: str
    line: int
