"""Builtins, `math` functions and methods for the symbolic executor.

Supported exactly: `abs`, `all`, `any`, `bool`, `dict` (keyword form), `enumerate`, `float`,
`int`, `isinstance`, `len`, `list`, `max`, `min`, `range` (constant bounds), `str`, `sum`,
`tuple`, `zip`; `math.sqrt`, `math.hypot`, `math.dist`, `math.fabs`, `math.floor`, `math.ceil`,
`math.trunc`, `math.isfinite`, `math.isinf`, `math.isnan`, `math.pi`, `math.e`; the methods
`dict.get/keys/values/items`, `list.append/extend` (on a local name) and
`str.lower/upper/strip/startswith/endswith`.

Over-approximated: `round` returns a fresh number within the rounding bound of its argument
(`|round(x, n) - x| <= 0.5 * 10**-n`), so a proof stays sound and a counterexample is confirmed by
a concrete run before anyone trusts it.

Refused (`UnsupportedError`): `sorted`, `min`/`max` with `key=`, trigonometry, logarithms and
exponentials, `math.inf`/`math.nan`, `str.split/join/index`, and every other name.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable, Mapping, Sequence
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Final

import z3

from rulecam.extensions import smt_ops
from rulecam.extensions.smt_values import (
    Failure,
    NumKind,
    SBool,
    SCallable,
    SConcept,
    SDict,
    SList,
    SMaybe,
    SNone,
    SNum,
    SStr,
    UnsupportedError,
    Value,
)

if TYPE_CHECKING:
    from rulecam.extensions.smt_exec import Executor, Path, Result

MAX_RANGE: Final = 100
TYPE_NAMES: Final = frozenset({"int", "float", "bool", "str", "list", "tuple", "dict"})
REFUSED_NAMES: Final = frozenset(
    {
        "sorted",
        "ArithmeticError",
        "Exception",
        "IndexError",
        "KeyError",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
    }
)
MATH_FUNCTIONS: Final = frozenset(
    {"sqrt", "hypot", "dist", "fabs", "floor", "ceil", "trunc", "isfinite", "isinf", "isnan"}
)

Builtin = Callable[["Executor", "Path", Sequence[Value], Mapping[str, Value], int], list["Result"]]


def math_attribute(attr: str, line: int) -> Value:
    """`math.<attr>`: a constant or a supported function.

    Args:
        attr: The attribute name.
        line: The source line.

    Returns:
        The value.

    Raises:
        UnsupportedError: For anything else in `math`.
    """
    if attr in {"pi", "e"}:
        exact = Fraction(getattr(math, attr))
        return SNum(z3.Q(exact.numerator, exact.denominator))
    if attr in MATH_FUNCTIONS:
        return SCallable(f"math.{attr}")
    raise UnsupportedError(f"math.{attr}", line)


def call(
    ex: Executor,
    path: Path,
    callee: SCallable,
    args: Sequence[Value],
    kwargs: Mapping[str, Value],
    *,
    line: int,
) -> list[Result]:
    """Call a builtin, a `math` function, a method or a module-level function.

    Args:
        ex: The executor.
        path: The path.
        callee: What is called.
        args: Positional arguments.
        kwargs: Keyword arguments.
        line: The source line.

    Returns:
        Each possible result (or failure) with its path.

    Raises:
        UnsupportedError: For calls the executor cannot encode.
    """
    name = callee.name
    if name.startswith("user."):
        if kwargs:
            raise UnsupportedError("keyword arguments to a function", line)
        return ex.call_user(path, name.removeprefix("user."), args, line)
    if name.startswith("method."):
        return _method(ex, path, callee, args, kwargs, line=line)
    handler = (
        _MATH.get(name.removeprefix("math.")) if name.startswith("math.") else BUILTINS.get(name)
    )
    if handler is None:
        raise UnsupportedError(f"calling `{name}`", line)
    return handler(ex, path, args, kwargs, line)


# -- helpers -------------------------------------------------------------------------------


def _arity(
    args: Sequence[Value], kwargs: Mapping[str, Value], count: int, name: str, line: int
) -> None:
    if kwargs or len(args) != count:
        raise UnsupportedError(f"`{name}` with these arguments", line)


def _numbers(
    ex: Executor, path: Path, values: Sequence[Value], line: int
) -> list[tuple[Path, tuple[SNum, ...] | Failure]]:
    states: list[tuple[Path, tuple[SNum, ...] | Failure]] = [(path, ())]
    for value in values:
        nxt: list[tuple[Path, tuple[SNum, ...] | Failure]] = []
        for current, done in states:
            if isinstance(done, Failure):
                nxt.append((current, done))
                continue
            nxt.extend(
                (p, n if isinstance(n, Failure) else (*done, n))
                for p, n in ex.as_number(current, value, line)
            )
        states = nxt
    return states


def _map_numbers(
    ex: Executor,
    path: Path,
    values: Sequence[Value],
    line: int,
    build: Callable[[Path, tuple[SNum, ...]], list[Result]],
) -> list[Result]:
    out: list[Result] = []
    for current, numbers in _numbers(ex, path, values, line):
        out.extend(
            [(current, numbers)] if isinstance(numbers, Failure) else build(current, numbers)
        )
    return out


def _not_none(
    ex: Executor,
    path: Path,
    value: Value,
    line: int,
    then: Callable[[Path, Value], list[Result]],
) -> list[Result]:
    if not isinstance(value, SMaybe):
        if isinstance(value, SNone):
            return [(path, Failure("TypeError", "the value is None", line))]
        return then(path, value)
    out: list[Result] = []
    for side, is_none in ex.fork(path, value.is_none):
        if is_none:
            out.append((side, Failure("TypeError", "the value is None", line)))
        else:
            out.extend(_not_none(ex, side, value.value, line, then))
    return out


def _elements(value: Value, line: int) -> tuple[Value, ...]:
    if isinstance(value, SList):
        return value.items
    if isinstance(value, SDict):
        return tuple(SStr(key) for key, _ in value.entries)
    if isinstance(value, SStr) and value.text is not None:
        return tuple(SStr(ch) for ch in value.text)
    raise UnsupportedError(f"iterating over {smt_ops.type_name(value)}", line)


# -- builtins ------------------------------------------------------------------------------


def _len(
    ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    _arity(args, kwargs, 1, "len", line)
    return _not_none(ex, path, args[0], line, lambda p, v: [(p, _length(v, line))])


def _length(value: Value, line: int) -> Value | Failure:
    if isinstance(value, SList):
        return SNum(z3.RealVal(len(value.items)), "int")
    if isinstance(value, SDict):
        return SNum(z3.RealVal(len(value.entries)), "int")
    if isinstance(value, SStr) and value.text is not None:
        return SNum(z3.RealVal(len(value.text)), "int")
    if isinstance(value, SConcept):
        expr: Any = z3.RealVal(len(value.names[-1]))
        for index, name in enumerate(value.names[:-1]):
            expr = z3.If(value.code == index, z3.RealVal(len(name)), expr)
        return SNum(expr, "int")
    if isinstance(value, SStr):
        raise UnsupportedError("the length of a computed string", line)
    return Failure("TypeError", f"{smt_ops.type_name(value)} has no len()", line)


def _abs(
    ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    _arity(args, kwargs, 1, "abs", line)
    return _map_numbers(
        ex,
        path,
        args,
        line,
        lambda p, n: [(p, SNum(z3.If(n[0].expr >= 0, n[0].expr, -n[0].expr), n[0].kind))],
    )


def _extreme_kind(numbers: Sequence[SNum]) -> NumKind:
    kinds = {n.kind for n in numbers}
    return kinds.pop() if len(kinds) == 1 else "num"


def _extreme(biggest: bool) -> Builtin:
    name = "max" if biggest else "min"

    def run(
        ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
    ) -> list[Result]:
        if set(kwargs) - {"default"} or (kwargs and len(args) != 1) or not args:
            raise UnsupportedError(f"`{name}` with these arguments", line)
        values = _elements(args[0], line) if len(args) == 1 else tuple(args)
        if not values:
            default = kwargs.get("default")
            return [
                (
                    path,
                    default
                    if default is not None
                    else Failure("ValueError", f"{name}() of an empty sequence", line),
                )
            ]

        def build(current: Path, numbers: tuple[SNum, ...]) -> list[Result]:
            best = numbers[0].expr
            for number in numbers[1:]:
                better = number.expr > best if biggest else number.expr < best
                best = z3.If(better, number.expr, best)
            return [(current, SNum(best, _extreme_kind(numbers)))]

        return _map_numbers(ex, path, values, line, build)

    return run


def _sum(
    ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    if set(kwargs) - {"start"} or not 1 <= len(args) <= 2:  # noqa: PLR2004 - sum(it[, start])
        raise UnsupportedError("`sum` with these arguments", line)
    start = args[1] if len(args) == 2 else kwargs.get("start", SNum(z3.RealVal(0), "int"))  # noqa: PLR2004

    def build(current: Path, numbers: tuple[SNum, ...]) -> list[Result]:
        total = (
            z3.Sum(numbers[0].expr, *[n.expr for n in numbers[1:]])
            if len(numbers) > 1
            else numbers[0].expr
        )
        return [(current, SNum(total, smt_ops.combine_kinds(*(n.kind for n in numbers))))]

    return _map_numbers(ex, path, (start, *_elements(args[0], line)), line, build)


def _any_all(conj: bool) -> Builtin:
    def run(
        _ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
    ) -> list[Result]:
        _arity(args, kwargs, 1, "all" if conj else "any", line)
        terms = [smt_ops.pure_truth(v, line) for v in _elements(args[0], line)]
        return [(path, SBool(z3.And(True, *terms) if conj else z3.Or(False, *terms)))]

    return run


def lazy_any_all(
    ex: Executor, path: Path, name: str, node: ast.GeneratorExp, line: int
) -> list[Result]:
    """`any(... for ...)` / `all(... for ...)`, stopping at the first deciding element.

    Args:
        ex: The executor.
        path: The path.
        name: `any` or `all`.
        node: The generator.
        line: The source line.

    Returns:
        Each possible SBool (or failure) with its path.
    """
    deciding = name == "any"

    def step(current: Path, acc: object, value: Value) -> list[tuple[Path, object, bool]]:
        out: list[tuple[Path, object, bool]] = []
        for side, flag in ex.truth(current, value, line):
            if isinstance(flag, Failure):
                out.append((side, flag, True))
            else:
                out.append((side, flag if flag == deciding else acc, flag == deciding))
        return out

    results = ex.fold(node, path, not deciding, step)
    return [
        (p, acc if isinstance(acc, Failure) else SBool(z3.BoolVal(bool(acc)))) for p, acc in results
    ]


def _bool(
    _ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    if not args:
        return [(path, SBool(z3.BoolVal(False)))]
    _arity(args, kwargs, 1, "bool", line)
    return [(path, SBool(smt_ops.pure_truth(args[0], line)))]


def _float(
    ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    _arity(args, kwargs, 1, "float", line)
    value = args[0]
    if isinstance(value, SStr):
        return [(path, _parse_number(value, float, line))]
    return _map_numbers(ex, path, args, line, lambda p, n: [(p, SNum(n[0].expr, "float"))])


def _int(
    ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    _arity(args, kwargs, 1, "int", line)
    value = args[0]
    if isinstance(value, SStr):
        return [(path, _parse_number(value, int, line))]
    return _map_numbers(ex, path, args, line, lambda p, n: [(p, SNum(truncate(n[0].expr), "int"))])


def _parse_number(value: SStr, kind: type[int] | type[float], line: int) -> Value | Failure:
    text = smt_ops.known_text(value, line)
    try:
        number = kind(text)
    except ValueError:
        return Failure("ValueError", f"cannot convert {text!r}", line)
    exact = Fraction(repr(number))
    return SNum(z3.Q(exact.numerator, exact.denominator), "int" if kind is int else "float")


def truncate(expr: Any) -> Any:
    """Python's `int(x)` on a real: rounds towards zero.

    Args:
        expr: A Z3 Real term.

    Returns:
        The truncated term (still a Real).
    """
    return z3.If(expr >= 0, z3.ToReal(z3.ToInt(expr)), -z3.ToReal(z3.ToInt(-expr)))


def _round(
    ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    if set(kwargs) - {"ndigits"} or not 1 <= len(args) + len(kwargs) <= 2:  # noqa: PLR2004
        raise UnsupportedError("`round` with these arguments", line)
    digits_value = args[1] if len(args) == 2 else kwargs.get("ndigits")  # noqa: PLR2004
    digits = (
        None
        if digits_value is None or isinstance(digits_value, SNone)
        else smt_ops.concrete_int(digits_value, line)
    )
    if digits is not None and digits < 0:
        raise UnsupportedError("round with negative ndigits", line)
    ex.approximations.add("round() is over-approximated by its rounding bound")

    def build(current: Path, numbers: tuple[SNum, ...]) -> list[Result]:
        x = numbers[0]
        rounded = ex.fresh_real("round")
        bound = z3.Q(1, 2 * 10 ** (digits or 0))
        facts = [rounded - x.expr <= bound, x.expr - rounded <= bound]
        if digits is None:
            facts.append(z3.IsInt(rounded))
        kind: NumKind = "int" if digits is None else x.kind
        return [(current.assume(*facts), SNum(rounded, kind))]

    return _map_numbers(ex, path, args[:1], line, build)


def _str(
    _ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    if not args:
        return [(path, SStr(""))]
    _arity(args, kwargs, 1, "str", line)
    value = args[0]
    if isinstance(value, SStr | SConcept):
        return [(path, value)]
    if isinstance(value, SNum) and value.kind == "int":
        term = z3.simplify(value.expr)
        if z3.is_rational_value(term) and term.denominator_as_long() == 1:
            return [(path, SStr(str(term.numerator_as_long())))]
    return [(path, SStr(None))]


def _sequence(kind: str) -> Builtin:
    def run(
        _ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
    ) -> list[Result]:
        if not args:
            return [(path, SList((), "list" if kind == "list" else "tuple"))]
        _arity(args, kwargs, 1, kind, line)
        return [(path, SList(_elements(args[0], line), "list" if kind == "list" else "tuple"))]

    return run


def _dict(
    _ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    if args:
        raise UnsupportedError("`dict` with positional arguments", line)
    return [(path, SDict(tuple(kwargs.items())))]


def _range(
    _ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    if kwargs or not 1 <= len(args) <= 3:  # noqa: PLR2004 - range(stop) .. range(a, b, step)
        raise UnsupportedError("`range` with these arguments", line)
    bounds = [smt_ops.concrete_int(a, line) for a in args]
    numbers = range(*bounds)
    if len(numbers) > MAX_RANGE:
        raise UnsupportedError(f"a range longer than {MAX_RANGE}", line)
    return [(path, SList(tuple(SNum(z3.RealVal(i), "int") for i in numbers)))]


def _enumerate(
    _ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    if set(kwargs) - {"start"} or len(args) != 1:
        raise UnsupportedError("`enumerate` with these arguments", line)
    start_value = kwargs.get("start")
    start = 0 if start_value is None else smt_ops.concrete_int(start_value, line)
    pairs = tuple(
        SList((SNum(z3.RealVal(start + i), "int"), item), "tuple")
        for i, item in enumerate(_elements(args[0], line))
    )
    return [(path, SList(pairs))]


def _zip(
    _ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    if kwargs:
        raise UnsupportedError("`zip` with keyword arguments", line)
    columns = [_elements(a, line) for a in args]
    rows = tuple(SList(row, "tuple") for row in zip(*columns, strict=False))
    return [(path, SList(rows))]


def _isinstance(
    _ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    _arity(args, kwargs, 2, "isinstance", line)
    kinds = args[1].items if isinstance(args[1], SList) else (args[1],)
    names: list[str] = []
    for kind in kinds:
        if not (isinstance(kind, SCallable) and kind.name in TYPE_NAMES):
            raise UnsupportedError(
                "isinstance with a type other than int/float/bool/str/list/tuple/dict", line
            )
        names.append(kind.name)
    return [(path, SBool(z3.Or(False, *[instance_term(args[0], n, line) for n in names])))]


def instance_term(value: Value, kind: str, line: int) -> Any:
    """`isinstance(value, <kind>)` as a Z3 Bool term.

    Args:
        value: The value.
        kind: A type name in `TYPE_NAMES`.
        line: The source line.

    Returns:
        The term.

    Raises:
        UnsupportedError: When a number's int-or-float kind depends on the inputs.
    """
    if isinstance(value, SMaybe):
        return z3.And(z3.Not(value.is_none), instance_term(value.value, kind, line))
    if isinstance(value, SNum) and kind in {"int", "float"}:
        if value.kind == "num":
            raise UnsupportedError(
                "isinstance on a number that is int or float depending on the inputs", line
            )
        return z3.BoolVal(value.kind == kind)
    matches = {
        "bool": isinstance(value, SBool),
        "int": isinstance(value, SBool),
        "float": False,
        "str": isinstance(value, SStr | SConcept),
        "list": isinstance(value, SList) and value.kind == "list",
        "tuple": isinstance(value, SList) and value.kind == "tuple",
        "dict": isinstance(value, SDict),
    }
    return z3.BoolVal(matches[kind])


BUILTINS: Final[Mapping[str, Builtin]] = {
    "abs": _abs,
    "all": _any_all(conj=True),
    "any": _any_all(conj=False),
    "bool": _bool,
    "dict": _dict,
    "enumerate": _enumerate,
    "float": _float,
    "int": _int,
    "isinstance": _isinstance,
    "len": _len,
    "list": _sequence("list"),
    "max": _extreme(biggest=True),
    "min": _extreme(biggest=False),
    "range": _range,
    "round": _round,
    "str": _str,
    "sum": _sum,
    "tuple": _sequence("tuple"),
    "zip": _zip,
}


# -- math ----------------------------------------------------------------------------------


def _sqrt(
    ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    _arity(args, kwargs, 1, "math.sqrt", line)
    return _map_numbers(ex, path, args, line, lambda p, n: smt_ops.square_root(ex, p, n[0], line))


def _hypot(
    ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    if kwargs or not args:
        raise UnsupportedError("`math.hypot` with these arguments", line)
    return _map_numbers(
        ex, path, args, line, lambda p, n: smt_ops.square_root(ex, p, _squares(n), line)
    )


def _squares(numbers: Sequence[SNum]) -> SNum:
    return SNum(
        z3.Sum(*[n.expr * n.expr for n in numbers])
        if len(numbers) > 1
        else numbers[0].expr * numbers[0].expr
    )


def _dist(
    ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    _arity(args, kwargs, 2, "math.dist", line)
    p, q = _elements(args[0], line), _elements(args[1], line)
    if len(p) != len(q) or not p:
        return [(path, Failure("ValueError", "both points must have the same dimension", line))]

    def build(current: Path, numbers: tuple[SNum, ...]) -> list[Result]:
        half = len(numbers) // 2
        diffs = [SNum(a.expr - b.expr) for a, b in zip(numbers[:half], numbers[half:], strict=True)]
        return smt_ops.square_root(ex, current, _squares(diffs), line)

    return _map_numbers(ex, path, (*p, *q), line, build)


def _rounding(kind: str) -> Builtin:
    def run(
        ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
    ) -> list[Result]:
        _arity(args, kwargs, 1, f"math.{kind}", line)

        def build(current: Path, numbers: tuple[SNum, ...]) -> list[Result]:
            x = numbers[0].expr
            if kind == "floor":
                expr = z3.ToReal(z3.ToInt(x))
            elif kind == "ceil":
                expr = -z3.ToReal(z3.ToInt(-x))
            else:
                expr = truncate(x)
            return [(current, SNum(expr, "int"))]

        return _map_numbers(ex, path, args, line, build)

    return run


def _fabs(
    ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
) -> list[Result]:
    _arity(args, kwargs, 1, "math.fabs", line)
    return _map_numbers(
        ex,
        path,
        args,
        line,
        lambda p, n: [(p, SNum(z3.If(n[0].expr >= 0, n[0].expr, -n[0].expr), "float"))],
    )


def _finite(answer: bool) -> Builtin:
    def run(
        ex: Executor, path: Path, args: Sequence[Value], kwargs: Mapping[str, Value], line: int
    ) -> list[Result]:
        _arity(args, kwargs, 1, "math.isfinite", line)
        return _map_numbers(ex, path, args, line, lambda p, _n: [(p, SBool(z3.BoolVal(answer)))])

    return run


_MATH: Final[Mapping[str, Builtin]] = {
    "sqrt": _sqrt,
    "hypot": _hypot,
    "dist": _dist,
    "fabs": _fabs,
    "floor": _rounding("floor"),
    "ceil": _rounding("ceil"),
    "trunc": _rounding("trunc"),
    "isfinite": _finite(answer=True),
    "isinf": _finite(answer=False),
    "isnan": _finite(answer=False),
}


# -- methods -------------------------------------------------------------------------------

_STRING_METHODS: Final = frozenset({"lower", "upper", "strip", "startswith", "endswith"})
# Methods Python has but the executor does not encode: refused, never guessed.
_STRING_OTHERS: Final = frozenset(
    {"split", "join", "index", "count", "replace", "find", "format", "rstrip", "lstrip"}
    | {"title", "capitalize", "isdigit", "isalpha", "rsplit", "partition", "casefold"}
    | {"removeprefix", "removesuffix", "zfill", "center", "ljust", "rjust", "splitlines"}
)
_SUPPORTED_METHODS: Final[Mapping[str, frozenset[str]]] = {
    "dict": frozenset({"get", "keys", "values", "items"}),
    "list": frozenset({"append", "extend"}),
    "tuple": frozenset(),
    "str": _STRING_METHODS,
}
_PYTHON_METHODS: Final[Mapping[str, frozenset[str]]] = {
    "dict": frozenset({"pop", "update", "copy", "setdefault", "popitem", "clear", "fromkeys"}),
    "list": frozenset({"count", "index", "pop", "sort", "copy", "insert", "remove", "reverse"}),
    "tuple": frozenset({"count", "index"}),
    "str": _STRING_OTHERS,
    "number": frozenset({"is_integer", "real", "imag", "conjugate", "as_integer_ratio"}),
}


def _method_kind(value: Value) -> str | None:
    if isinstance(value, SDict):
        return "dict"
    if isinstance(value, SList):
        return value.kind
    if isinstance(value, SStr | SConcept):
        return "str"
    if isinstance(value, SNum | SBool):
        return "number"
    return None


def method_problem(base: Value, attr: str, line: int) -> Failure | None:
    """Why `base.attr` cannot be looked up: an AttributeError, or None when it is supported.

    Args:
        base: The receiver (never None-able: callers fork on None first).
        attr: The attribute name.
        line: The source line.

    Returns:
        The AttributeError Python raises, or None for a supported method.

    Raises:
        UnsupportedError: For a method Python has but the executor does not encode.
    """
    kind = _method_kind(base)
    if kind is not None and attr in _SUPPORTED_METHODS.get(kind, frozenset()):
        return None
    if kind is not None and attr in _PYTHON_METHODS.get(kind, frozenset()):
        raise UnsupportedError(f"`{kind}.{attr}`", line)
    return Failure("AttributeError", f"{smt_ops.type_name(base)} has no .{attr}", line)


def _method(
    ex: Executor,
    path: Path,
    callee: SCallable,
    args: Sequence[Value],
    kwargs: Mapping[str, Value],
    *,
    line: int,
) -> list[Result]:
    name = callee.name.removeprefix("method.")
    receiver = callee.receiver
    assert receiver is not None  # noqa: S101 - methods always carry their receiver
    if kwargs:
        raise UnsupportedError(f"`.{name}` with keyword arguments", line)

    def apply(current: Path, value: Value) -> list[Result]:
        if isinstance(value, SDict):
            return _dict_method(current, value, name, args, line)
        if isinstance(value, SList):
            return _list_method(current, value, callee.receiver_var, name, args, line=line)
        if isinstance(value, SStr | SConcept):
            return [(current, _string_method(value, name, args, line))]
        return [
            (current, Failure("AttributeError", f"{smt_ops.type_name(value)} has no .{name}", line))
        ]

    return _not_none(ex, path, receiver, line, apply)


def _dict_method(
    path: Path, value: SDict, name: str, args: Sequence[Value], line: int
) -> list[Result]:
    if name == "get" and 1 <= len(args) <= 2:  # noqa: PLR2004 - get(key[, default])
        found = value.lookup(smt_ops.known_text(args[0], line))
        return [(path, found if found is not None else (args[1] if len(args) == 2 else SNone()))]  # noqa: PLR2004
    if args:
        raise UnsupportedError(f"`dict.{name}` with these arguments", line)
    if name == "keys":
        return [(path, SList(tuple(SStr(k) for k, _ in value.entries)))]
    if name == "values":
        return [(path, SList(tuple(v for _, v in value.entries)))]
    if name == "items":
        return [(path, SList(tuple(SList((SStr(k), v), "tuple") for k, v in value.entries)))]
    raise UnsupportedError(f"`dict.{name}`", line)


def _list_method(
    path: Path, value: SList, var: str | None, name: str, args: Sequence[Value], *, line: int
) -> list[Result]:
    if name not in {"append", "extend"} or len(args) != 1:
        raise UnsupportedError(f"`list.{name}`", line)
    if var is None or var not in path.env or value.kind != "list":
        raise UnsupportedError(f"`.{name}` on something other than a local list", line)
    added = (args[0],) if name == "append" else _elements(args[0], line)
    return [(path.bind(var, SList(value.items + added)), SNone())]


def _string_method(value: SStr | SConcept, name: str, args: Sequence[Value], line: int) -> Value:
    transforms: dict[str, Callable[[str], str]] = {
        "lower": str.lower,
        "upper": str.upper,
        "strip": str.strip,
    }
    if name in transforms and not args:
        change = transforms[name]
        if isinstance(value, SConcept):
            return SConcept(value.code, tuple(change(n) for n in value.names))
        return SStr(None if value.text is None else change(value.text))
    if name in {"startswith", "endswith"} and len(args) == 1:
        piece = smt_ops.known_text(args[0], line)
        test: Callable[[str], bool] = (
            (lambda s: s.startswith(piece))
            if name == "startswith"
            else (lambda s: s.endswith(piece))
        )
        if isinstance(value, SConcept):
            return SBool(smt_ops.concept_matches(value, test))
        return SBool(z3.BoolVal(test(smt_ops.known_text(value, line))))
    raise UnsupportedError(f"`str.{name}`", line)
