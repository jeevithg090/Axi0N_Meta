"""Operators for the symbolic executor: arithmetic, comparisons, truth values and subscripts.

Every function mirrors Python's semantics on the value kinds of `smt_values`, forking through the
executor where Python would raise on some inputs only (a zero divisor, a None operand, a missing
index), and raising `UnsupportedError` where an exact encoding is out of reach.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

import z3

from rulecam.extensions.smt_values import (
    Failure,
    NumKind,
    SBool,
    SCallable,
    SConcept,
    SDict,
    SList,
    SMaybe,
    SModule,
    SNone,
    SNum,
    SStr,
    UnsupportedError,
    Value,
)

if TYPE_CHECKING:
    from rulecam.extensions.smt_exec import Executor, Path, Result

MAX_POWER: Final = 4

_TYPE_NAMES: Final[dict[type, str]] = {
    SNum: "a number",
    SBool: "a bool",
    SStr: "a string",
    SConcept: "a string",
    SNone: "None",
    SMaybe: "a value that may be None",
    SList: "a list",
    SDict: "a dict",
    SCallable: "a function",
    SModule: "a module",
}


def type_name(value: Value) -> str:
    """A value's Python type in plain words, for failure messages.

    Args:
        value: The value.

    Returns:
        The words.
    """
    return _TYPE_NAMES.get(type(value), "a value")


def pure_truth(value: Value, line: int) -> Any:
    """Python truthiness of `value` as one Z3 Bool term (no forking needed).

    Args:
        value: The value.
        line: The source line.

    Returns:
        The term.

    Raises:
        UnsupportedError: For a string whose text is unknown.
    """
    if isinstance(value, SMaybe):
        return z3.And(z3.Not(value.is_none), pure_truth(value.value, line))
    if isinstance(value, SStr) and value.text is None:
        raise UnsupportedError("the truth value of a computed string", line)
    truth = _TRUTH.get(type(value))
    return truth(value) if truth is not None else z3.BoolVal(True)


_TRUTH: Final[dict[type, Callable[[Any], Any]]] = {
    SBool: lambda v: v.expr,
    SNum: lambda v: v.expr != 0,
    SNone: lambda _v: z3.BoolVal(False),
    SList: lambda v: z3.BoolVal(bool(v.items)),
    SDict: lambda v: z3.BoolVal(bool(v.entries)),
    SStr: lambda v: z3.BoolVal(bool(v.text)),
}


def combine_kinds(*kinds: NumKind) -> NumKind:
    """The kind of `a <op> b` for + - * (int only when every operand is an int).

    Args:
        kinds: The operands' kinds.

    Returns:
        `int`, `float`, or `num` when it depends on values (unknown int-or-float).
    """
    if all(k == "int" for k in kinds):
        return "int"
    if any(k == "float" for k in kinds):
        return "float"
    return "num"


def binary(
    ex: Executor, path: Path, op: ast.operator, left: Value, right: Value, *, line: int
) -> list[Result]:
    """`left <op> right`.

    Args:
        ex: The executor (for forking).
        path: The path.
        op: The operator.
        left: The left operand.
        right: The right operand.
        line: The source line.

    Returns:
        Each possible value (or failure) with its path.

    Raises:
        UnsupportedError: For an operator or operand types the executor cannot encode.
    """
    if isinstance(op, ast.Add) and isinstance(left, SList) and isinstance(right, SList):
        return [(path, SList(left.items + right.items, left.kind))]
    if isinstance(op, ast.Add) and isinstance(left, SStr) and isinstance(right, SStr):
        text = None if left.text is None or right.text is None else left.text + right.text
        return [(path, SStr(text))]
    if not isinstance(op, ast.Add | ast.Sub | ast.Mult | ast.Div | ast.Pow):
        raise UnsupportedError(f"the operator {type(op).__name__}", line)
    out: list[Result] = []
    for current, a in ex.as_number(path, left, line):
        if isinstance(a, Failure):
            out.append((current, a))
            continue
        for side, b in ex.as_number(current, right, line):
            out.extend(
                [(side, b)] if isinstance(b, Failure) else _arith(ex, side, op, a, b, line=line)
            )
    return out


def _arith(
    ex: Executor, path: Path, op: ast.operator, a: SNum, b: SNum, *, line: int
) -> list[Result]:
    if isinstance(op, ast.Add):
        return [(path, SNum(a.expr + b.expr, combine_kinds(a.kind, b.kind)))]
    if isinstance(op, ast.Sub):
        return [(path, SNum(a.expr - b.expr, combine_kinds(a.kind, b.kind)))]
    if isinstance(op, ast.Mult):
        return [(path, SNum(a.expr * b.expr, combine_kinds(a.kind, b.kind)))]
    if isinstance(op, ast.Div):
        return [
            (
                side,
                Failure("ZeroDivisionError", "division by zero", line)
                if zero
                else SNum(a.expr / b.expr),
            )
            for side, zero in ex.fork(path, b.expr == 0)
        ]
    return _power(ex, path, a, b, line)


def _power(ex: Executor, path: Path, base: SNum, exponent: SNum, line: int) -> list[Result]:
    value = z3.simplify(exponent.expr)
    if not z3.is_rational_value(value):
        raise UnsupportedError("** with a non-constant exponent", line)
    as_fraction = (value.numerator_as_long(), value.denominator_as_long())
    if as_fraction == (1, 2):
        return square_root(ex, path, base, line)
    power = as_fraction[0]
    if as_fraction[1] != 1 or not 0 <= power <= MAX_POWER:
        raise UnsupportedError(f"** with exponent {value}", line)
    result: Any = z3.RealVal(1)
    for _ in range(power):
        result = result * base.expr
    kind: NumKind = base.kind if exponent.kind == "int" else "float"
    return [(path, SNum(result, kind))]


def square_root(ex: Executor, path: Path, value: SNum, line: int) -> list[Result]:
    """`math.sqrt(value)`, defined exactly: a fresh `s >= 0` with `s * s == value`.

    Args:
        ex: The executor.
        path: The path.
        value: The operand.
        line: The source line.

    Returns:
        The root on paths where `value >= 0`, a ValueError where it is negative.
    """
    out: list[Result] = []
    for side, negative in ex.fork(path, value.expr < 0):
        if negative:
            out.append(
                (side, Failure("ValueError", "math domain error (sqrt of a negative)", line))
            )
            continue
        root = ex.fresh_real("sqrt")
        out.append((side.assume(root >= 0, root * root == value.expr), SNum(root)))
    return out


def unary(ex: Executor, path: Path, op: ast.unaryop, operand: Value, line: int) -> list[Result]:
    """`<op> operand` for `not`, `-` and `+`.

    Args:
        ex: The executor.
        path: The path.
        op: The operator.
        operand: The operand.
        line: The source line.

    Returns:
        Each possible value (or failure) with its path.

    Raises:
        UnsupportedError: For `~`.
    """
    if isinstance(op, ast.Not):
        return [(path, SBool(z3.Not(pure_truth(operand, line))))]
    if not isinstance(op, ast.USub | ast.UAdd):
        raise UnsupportedError(f"the operator {type(op).__name__}", line)
    out: list[Result] = []
    for current, number in ex.as_number(path, operand, line):
        if isinstance(number, Failure):
            out.append((current, number))
        else:
            expr = -number.expr if isinstance(op, ast.USub) else number.expr
            out.append((current, SNum(expr, number.kind)))
    return out


def compare(
    ex: Executor, path: Path, op: ast.cmpop, left: Value, right: Value, *, line: int
) -> list[Result]:
    """`left <op> right` for one comparison operator.

    Args:
        ex: The executor.
        path: The path.
        op: The comparison operator.
        left: The left operand.
        right: The right operand.
        line: The source line.

    Returns:
        Each possible SBool (or failure) with its path.

    Raises:
        UnsupportedError: For comparisons the executor cannot encode.
    """
    if isinstance(op, ast.Is | ast.IsNot):
        term = _is_none(left, right, line)
        return [(path, SBool(term if isinstance(op, ast.Is) else z3.Not(term)))]
    if isinstance(op, ast.Eq | ast.NotEq):
        term = equal(left, right, line)
        return [(path, SBool(term if isinstance(op, ast.Eq) else z3.Not(term)))]
    if isinstance(op, ast.In | ast.NotIn):
        term = contains(right, left, line)
        return [(path, SBool(term if isinstance(op, ast.In) else z3.Not(term)))]
    return _order(ex, path, op, left, right, line=line)


def _order(
    ex: Executor, path: Path, op: ast.cmpop, left: Value, right: Value, *, line: int
) -> list[Result]:
    if isinstance(left, SStr | SConcept | SList) or isinstance(right, SStr | SConcept | SList):
        raise UnsupportedError("ordering strings or lists", line)
    out: list[Result] = []
    for current, a in ex.as_number(path, left, line):
        if isinstance(a, Failure):
            out.append((current, a))
            continue
        for side, b in ex.as_number(current, right, line):
            out.append((side, b if isinstance(b, Failure) else SBool(_ordered(op, a.expr, b.expr))))
    return out


def _ordered(op: ast.cmpop, a: Any, b: Any) -> Any:
    if isinstance(op, ast.Lt):
        return a < b
    if isinstance(op, ast.LtE):
        return a <= b
    if isinstance(op, ast.Gt):
        return a > b
    return a >= b


def _is_none(left: Value, right: Value, line: int) -> Any:
    if isinstance(right, SNone):
        return none_term(left)
    if isinstance(left, SNone):
        return none_term(right)
    raise UnsupportedError("`is` with something other than None", line)


def none_term(value: Value) -> Any:
    """`value is None` as a Z3 Bool term.

    Args:
        value: The value.

    Returns:
        The term.
    """
    if isinstance(value, SNone):
        return z3.BoolVal(True)
    if isinstance(value, SMaybe):
        return value.is_none
    return z3.BoolVal(False)


def equal(left: Value, right: Value, line: int) -> Any:
    """`left == right` as a Z3 Bool term (Python's cross-type equality is False).

    Args:
        left: The left operand.
        right: The right operand.
        line: The source line.

    Returns:
        The term.

    Raises:
        UnsupportedError: For strings whose text is unknown.
    """
    optional = _optional_equal(left, right, line)
    if optional is not None:
        return optional
    if _numeric(left) and _numeric(right):
        return _as_term(left) == _as_term(right)
    if _stringy(left) and _stringy(right):
        return _string_equal(left, right, line)
    return _sequence_equal(left, right, line)


def _optional_equal(left: Value, right: Value, line: int) -> Any | None:
    """Equality when either side may be None (None == None is True); None otherwise."""
    if not isinstance(left, SNone | SMaybe) and not isinstance(right, SNone | SMaybe):
        return None
    both_none = z3.And(none_term(left), none_term(right))
    inner_left = left.value if isinstance(left, SMaybe) else left
    inner_right = right.value if isinstance(right, SMaybe) else right
    if isinstance(inner_left, SNone) or isinstance(inner_right, SNone):
        return both_none
    present = z3.And(
        z3.Not(none_term(left)), z3.Not(none_term(right)), equal(inner_left, inner_right, line)
    )
    return z3.Or(both_none, present)


def _sequence_equal(left: Value, right: Value, line: int) -> Any:
    if isinstance(left, SDict) or isinstance(right, SDict):
        raise UnsupportedError("comparing dicts", line)
    if not (isinstance(left, SList) and isinstance(right, SList)):
        return z3.BoolVal(False)
    if len(left.items) != len(right.items):
        return z3.BoolVal(False)
    pairs = zip(left.items, right.items, strict=True)
    return z3.And(True, *[equal(a, b, line) for a, b in pairs])


def _numeric(value: Value) -> bool:
    return isinstance(value, SNum | SBool)


def _as_term(value: Value) -> Any:
    if isinstance(value, SBool):
        return z3.If(value.expr, z3.RealVal(1), z3.RealVal(0))
    assert isinstance(value, SNum)  # noqa: S101 - callers check `_numeric`
    return value.expr


def _stringy(value: Value) -> bool:
    return isinstance(value, SStr | SConcept)


def _string_equal(left: Value, right: Value, line: int) -> Any:
    if isinstance(left, SConcept) and isinstance(right, SConcept):
        pairs = [
            z3.And(left.code == i, right.code == j)
            for i, a in enumerate(left.names)
            for j, b in enumerate(right.names)
            if a == b
        ]
        return z3.Or(False, *pairs)
    if isinstance(left, SConcept) or isinstance(right, SConcept):
        concept, text = (left, right) if isinstance(left, SConcept) else (right, left)
        assert isinstance(concept, SConcept)  # noqa: S101 - just narrowed
        return concept_matches(concept, lambda name: name == known_text(text, line))
    return z3.BoolVal(known_text(left, line) == known_text(right, line))


def known_text(value: Value, line: int) -> str:
    """The text of a string whose text is known.

    Args:
        value: An SStr.
        line: The source line.

    Returns:
        The text.

    Raises:
        UnsupportedError: When the text is unknown or the value is not a known string.
    """
    if isinstance(value, SStr) and value.text is not None:
        return value.text
    raise UnsupportedError("a string whose text is computed", line)


def concept_matches(concept: SConcept, test: Any) -> Any:
    """The concept's name satisfies `test` (a Python predicate over names), as a Z3 Bool term.

    Args:
        concept: The concept.
        test: `name -> bool`.

    Returns:
        The term.
    """
    return z3.Or(False, *[concept.code == i for i, name in enumerate(concept.names) if test(name)])


def contains(container: Value, element: Value, line: int) -> Any:
    """`element in container` as a Z3 Bool term.

    Args:
        container: A list, tuple, dict (keys) or string.
        element: The element.
        line: The source line.

    Returns:
        The term.

    Raises:
        UnsupportedError: For containers the executor cannot encode.
    """
    if isinstance(container, SList):
        return z3.Or(False, *[equal(element, item, line) for item in container.items])
    if isinstance(container, SDict):
        return z3.BoolVal(known_text(element, line) in {k for k, _ in container.entries})
    if isinstance(container, SConcept):
        piece = known_text(element, line)
        return concept_matches(container, lambda name: piece in name)
    if isinstance(container, SStr):
        if isinstance(element, SConcept):
            text = known_text(container, line)
            return concept_matches(element, lambda name: name in text)
        return z3.BoolVal(known_text(element, line) in known_text(container, line))
    raise UnsupportedError(f"`in` on {type_name(container)}", line)


def concrete_int(value: Value, line: int) -> int:
    """A number that must be a concrete integer (a list index, a range bound).

    Args:
        value: The value.
        line: The source line.

    Returns:
        The integer.

    Raises:
        UnsupportedError: When it is symbolic or not an integer.
    """
    term = z3.simplify(value.expr) if isinstance(value, SNum) else None
    if term is None or not z3.is_rational_value(term) or term.denominator_as_long() != 1:
        raise UnsupportedError("an index or bound that is not a constant integer", line)
    return int(term.numerator_as_long())


def subscript(ex: Executor, path: Path, base: Value, key: Value, line: int) -> list[Result]:
    """`base[key]`.

    Args:
        ex: The executor.
        path: The path.
        base: The container.
        key: The key or index.
        line: The source line.

    Returns:
        The element (or a KeyError / IndexError / TypeError failure) with its path.

    Raises:
        UnsupportedError: For symbolic indices or unsupported containers.
    """
    if isinstance(base, SMaybe):
        out: list[Result] = []
        for side, is_none in ex.fork(path, base.is_none):
            if is_none:
                out.append((side, Failure("TypeError", "None is not subscriptable", line)))
            else:
                out.extend(subscript(ex, side, base.value, key, line))
        return out
    if isinstance(base, SDict):
        name = known_text(key, line)
        found = base.lookup(name)
        return [(path, found if found is not None else Failure("KeyError", repr(name), line))]
    if isinstance(base, SList):
        index = concrete_int(key, line)
        if -len(base.items) <= index < len(base.items):
            return [(path, base.items[index])]
        return [(path, Failure("IndexError", "list index out of range", line))]
    if isinstance(base, SNone):
        return [(path, Failure("TypeError", "None is not subscriptable", line))]
    raise UnsupportedError(f"subscripting {type_name(base)}", line)


def slice_list(base: Value, bounds: tuple[int | None, int | None, int | None], line: int) -> Value:
    """`base[lower:upper:step]` with constant bounds.

    Args:
        base: The list.
        bounds: The constant lower, upper and step.
        line: The source line.

    Returns:
        The sliced list.

    Raises:
        UnsupportedError: When `base` is not a list.
    """
    if not isinstance(base, SList):
        raise UnsupportedError(f"slicing {type_name(base)}", line)
    lower, upper, step = bounds
    return SList(base.items[lower:upper:step], base.kind)


def store_item(container: Value, key: Value, value: Value, line: int) -> Value | Failure:
    """`container[key] = value` on a dict or list held by a plain name.

    Args:
        container: The dict or list.
        key: The key or index.
        value: The new value.
        line: The source line.

    Returns:
        The updated container, or an IndexError failure.

    Raises:
        UnsupportedError: For tuples, strings and other containers.
    """
    if isinstance(container, SDict):
        return container.with_entry(known_text(key, line), value)
    if isinstance(container, SList) and container.kind == "list":
        index = concrete_int(key, line)
        if not -len(container.items) <= index < len(container.items):
            return Failure("IndexError", "list assignment index out of range", line)
        items = list(container.items)
        items[index] = value
        return SList(tuple(items))
    raise UnsupportedError(f"item assignment on {type_name(container)}", line)
