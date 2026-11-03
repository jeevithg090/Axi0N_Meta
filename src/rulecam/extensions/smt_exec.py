"""Forking symbolic execution of a model-written check (`extensions.smt`).

`Executor.run(sample)` interprets the check's Python over a symbolic sample. Whenever control flow
depends on a symbolic truth value (`if`, `and`/`or`, a comprehension filter, a possible None, a
possible zero divisor), the path forks in two, each side carrying its condition in `Path.pc`. A
side whose condition contradicts what is already known is pruned with a quick solver check. Every
finished path becomes an `Outcome`: the condition under which it runs, and either the Z3 term of
the result's `value` or the `Failure` it ends in (an exception, or a result that is not a valid
`CheckResult`).

Exact or refused: each construct is encoded exactly, refused with `UnsupportedError`, or (only
`round`) over-approximated by a fresh value within the rounding bound, which keeps proofs sound;
`approximations` names what was over-approximated, and a counterexample is only trusted after a
concrete run reproduces it (`extensions.smt`). Numbers are exact reals: float rounding is ignored.
"""

from __future__ import annotations

import ast
import itertools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Any, Final

import z3

from rulecam.extensions import smt_builtins, smt_ops
from rulecam.extensions.smt_values import (
    Failure,
    PathLimitError,
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

ENTRY: Final = "check"
MAX_CALL_DEPTH: Final = 8
MAX_WHY_CHARS: Final = 120  # schema.MAX_WHY_CHARS
# Fresh solver variables (sqrt, round) must never share a name across executors: two runs in one
# query (before/after a move) would otherwise be tied together.
_FRESH: Final = itertools.count(1)
_RESULT_KEYS: Final = frozenset({"value", "measure", "why"})


@dataclass(frozen=True)
class Path:
    """One execution path: its condition so far and its variables.

    Attributes:
        pc: Z3 Bool terms that hold on this path.
        env: The current function's variables.
        local_names: Names the current function assigns (read before assignment = NameError).
    """

    pc: tuple[Any, ...] = ()
    env: Mapping[str, Value] = field(default_factory=dict)
    local_names: frozenset[str] = frozenset()

    def assume(self, *terms: Any) -> Path:
        """This path with `terms` added to its condition."""
        return replace(self, pc=(*self.pc, *terms))

    def bind(self, name: str, value: Value) -> Path:
        """This path with `name` bound to `value`."""
        return replace(self, env={**self.env, name: value})


@dataclass(frozen=True)
class FellThrough:
    """A statement finished normally."""


@dataclass(frozen=True)
class Returned:
    """A `return` ran."""

    value: Value


@dataclass(frozen=True)
class Broke:
    """A `break` ran."""


@dataclass(frozen=True)
class Continued:
    """A `continue` ran."""


Flow = FellThrough | Returned | Broke | Continued | Failure
NORMAL: Final = FellThrough()
Result = tuple[Path, Value | Failure]
FlowResult = tuple[Path, Flow]


@dataclass(frozen=True)
class Outcome:
    """One finished path of a `check(sample)` call.

    Attributes:
        pc: The path's condition.
        value: The Z3 Bool term of the result's `value` (None on failure).
        failure: The failure the path ends in (None when it returns a valid result).
    """

    pc: tuple[Any, ...]
    value: Any | None
    failure: Failure | None


@dataclass(frozen=True)
class Program:
    """A parsed check module.

    Attributes:
        tree: The module's syntax tree.
        functions: Its top-level functions by name.
        literals: Every string constant in it (they join the concept universe).
    """

    tree: ast.Module
    functions: Mapping[str, ast.FunctionDef]
    literals: frozenset[str]


def parse_program(code: str) -> Program:
    """Parse a check and refuse what the executor cannot encode, before running anything.

    Args:
        code: The check module's source.

    Returns:
        The program.

    Raises:
        UnsupportedError: For a syntax error, a missing `check`, or an unsupported construct.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise UnsupportedError("the code does not parse", exc.lineno or 0) from exc
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    if ENTRY not in functions or len(functions[ENTRY].args.args) != 1:
        raise UnsupportedError("no `def check(sample)`", 0)
    for node in ast.walk(tree):
        _refuse(node)
    literals = frozenset(
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )
    return Program(tree=tree, functions=functions, literals=literals)


_REFUSED: Final[Mapping[type[ast.AST], str]] = {
    ast.While: "a while loop",
    ast.Lambda: "a lambda",
    ast.Set: "a set",
    ast.SetComp: "a set comprehension",
    ast.DictComp: "a dict comprehension",
    ast.Starred: "a starred expression",
    ast.Try: "try/except",
    ast.Raise: "raise",
    ast.Assert: "assert",
    ast.With: "with",
    ast.ClassDef: "a class",
    ast.Global: "global",
    ast.Nonlocal: "nonlocal",
    ast.Delete: "del",
    ast.ImportFrom: "from-import",
    ast.Yield: "yield",
    ast.Await: "await",
    ast.Match: "match",
}
_REFUSED_OPERATORS: Final[Mapping[type[ast.AST], str]] = {
    ast.FloorDiv: "floor division (//)",
    ast.Mod: "the % operator",
    ast.MatMult: "the @ operator",
    ast.BitAnd: "a bitwise operator",
    ast.BitOr: "a bitwise operator",
    ast.BitXor: "a bitwise operator",
    ast.LShift: "a bit shift",
    ast.RShift: "a bit shift",
    ast.Invert: "the ~ operator",
}


def _refuse(node: ast.AST) -> None:
    line = getattr(node, "lineno", 0)
    reason = _REFUSED.get(type(node)) or _REFUSED_OPERATORS.get(type(node))
    if reason is not None:
        raise UnsupportedError(reason, line)
    if isinstance(node, ast.Name) and node.id in smt_builtins.REFUSED_NAMES:
        raise UnsupportedError(f"`{node.id}`", line)
    if isinstance(node, ast.Import) and any(a.name != "math" for a in node.names):
        raise UnsupportedError("an import other than math", line)


def local_names(func: ast.FunctionDef) -> frozenset[str]:
    """The names a function binds: its parameters and assignment targets.

    Comprehension variables are left out: they live in their own scope.

    Args:
        func: The function.

    Returns:
        The names.
    """
    names = {a.arg for a in func.args.args}
    for node in ast.walk(func):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign | ast.For | ast.NamedExpr):
            targets = [node.target]
        for target in targets:
            names.update(_bound_by(target))
    return frozenset(names)


def _bound_by(target: ast.expr) -> set[str]:
    """Names an assignment target binds: `x` and unpacked `a, b`, but not `d` in `d[k] = v`."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Tuple | ast.List):
        return {name for elt in target.elts for name in _bound_by(elt)}
    return set()


class Executor:
    """Runs one program's `check` symbolically (see the module docstring)."""

    def __init__(
        self,
        program: Program,
        assumptions: Sequence[Any],
        *,
        max_paths: int,
        check_ms: int = 500,
    ) -> None:
        """Prepare a run.

        Args:
            program: The parsed check.
            assumptions: Facts about the inputs (validity), used to prune impossible paths.
            max_paths: The path budget for one run.
            check_ms: Solver timeout for each pruning check.
        """
        self.program = program
        self.approximations: set[str] = set()
        self._max_paths = max_paths
        self._paths = 1
        self._depth = 0
        self._solver = z3.Solver()
        self._solver.set("timeout", check_ms)
        self._solver.add(*assumptions)
        self._globals: dict[str, Value] = {}
        self._globals = self._module_globals()

    # -- running ---------------------------------------------------------------------------

    def run(self, sample: SDict) -> list[Outcome]:
        """Run `check(sample)` on every path.

        Args:
            sample: The symbolic sample dict.

        Returns:
            One outcome per finished path.

        Raises:
            UnsupportedError: When the check uses something the executor cannot encode.
            PathLimitError: When the run exceeds its path budget.
        """
        results = self.call_user(Path(), ENTRY, (sample,), 0)
        outcomes: list[Outcome] = []
        for path, value in results:
            if isinstance(value, Failure):
                outcomes.append(Outcome(path.pc, None, value))
                continue
            for checked, verdict in self.result_value(path, value):
                if isinstance(verdict, Failure):
                    outcomes.append(Outcome(checked.pc, None, verdict))
                else:
                    outcomes.append(Outcome(checked.pc, verdict, None))
        return outcomes

    def _module_globals(self) -> dict[str, Value]:
        path = Path(env={"math": SModule("math")})
        for stmt in self.program.tree.body:
            if isinstance(stmt, ast.FunctionDef):
                # Defined in order: a constant can only call functions defined above it.
                path = path.bind(stmt.name, SCallable(f"user.{stmt.name}"))
            elif isinstance(stmt, ast.Assign | ast.AnnAssign):
                flows = self.exec_stmt(stmt, path)
                if len(flows) != 1 or not isinstance(flows[0][1], FellThrough):
                    raise UnsupportedError("top-level code that is not a constant", stmt.lineno)
                path = flows[0][0]
        return dict(path.env)

    def call_user(self, path: Path, name: str, args: Sequence[Value], line: int) -> list[Result]:
        """Call a module-level function on every path.

        Args:
            path: The caller's path.
            name: The function's name.
            args: Positional arguments.
            line: The call's line.

        Returns:
            One result per path, in the caller's scope.
        """
        func = self.program.functions[name]
        params = [a.arg for a in func.args.args]
        if len(params) != len(args):
            return [(path, Failure("TypeError", f"{name}() takes {len(params)} arguments", line))]
        if self._depth >= MAX_CALL_DEPTH:
            raise UnsupportedError("recursion or calls nested too deep", line)
        inner = Path(pc=path.pc, env=dict(zip(params, args, strict=True)))
        inner = replace(inner, local_names=local_names(func))
        self._depth += 1
        try:
            flows = self.exec_block(func.body, inner)
        finally:
            self._depth -= 1
        out: list[Result] = []
        for done, flow in flows:
            back = replace(path, pc=done.pc)
            if isinstance(flow, Returned):
                out.append((back, flow.value))
            elif isinstance(flow, Failure):
                out.append((back, flow))
            else:
                out.append((back, SNone()))
        return out

    # -- forking ---------------------------------------------------------------------------

    def fork(self, path: Path, cond: Any) -> list[tuple[Path, bool]]:
        """Split `path` on a Z3 Bool term, keeping only the sides that can happen.

        Args:
            path: The path.
            cond: The condition.

        Returns:
            `(path with cond, True)` and/or `(path with not cond, False)`.
        """
        cond = z3.simplify(cond)
        if z3.is_true(cond):
            return [(path, True)]
        if z3.is_false(cond):
            return [(path, False)]
        sides = [
            (path.assume(term), flag)
            for term, flag in ((cond, True), (z3.Not(cond), False))
            if self._possible(path.pc, term)
        ]
        self._paths += max(len(sides) - 1, 0)
        if self._paths > self._max_paths:
            msg = f"more than {self._max_paths} paths"
            raise PathLimitError(msg)
        return sides

    def _possible(self, pc: tuple[Any, ...], term: Any) -> bool:
        self._solver.push()
        self._solver.add(*pc, term)
        answer = self._solver.check()
        self._solver.pop()
        return bool(answer != z3.unsat)

    def fresh_real(self, label: str) -> Any:
        """A new Z3 Real variable (for sqrt, round and similar definitions).

        Args:
            label: A readable name part.

        Returns:
            The variable.
        """
        return z3.Real(f"{label}_{next(_FRESH)}")

    def truth(self, path: Path, value: Value, line: int) -> list[tuple[Path, bool | Failure]]:
        """Python truthiness of `value`, forking where it is symbolic.

        Args:
            path: The path.
            value: The value.
            line: The source line.

        Returns:
            Each possible truth value with its path.
        """
        if isinstance(value, SMaybe):
            out: list[tuple[Path, bool | Failure]] = []
            for side, is_none in self.fork(path, value.is_none):
                out.extend([(side, False)] if is_none else self.truth(side, value.value, line))
            return out
        term = smt_ops.pure_truth(value, line)
        return list(self.fork(path, term))

    def as_number(self, path: Path, value: Value, line: int) -> list[tuple[Path, SNum | Failure]]:
        """`value` as a number, or a TypeError where Python would raise one.

        Args:
            path: The path.
            value: The value.
            line: The source line.

        Returns:
            Each possible number (or failure) with its path.
        """
        if isinstance(value, SNum):
            return [(path, value)]
        if isinstance(value, SBool):
            return [(path, SNum(z3.If(value.expr, z3.RealVal(1), z3.RealVal(0)), "int"))]
        if isinstance(value, SMaybe):
            out: list[tuple[Path, SNum | Failure]] = []
            for side, is_none in self.fork(path, value.is_none):
                if is_none:
                    out.append((side, Failure("TypeError", "a number was None", line)))
                else:
                    out.extend(self.as_number(side, value.value, line))
            return out
        kind = smt_ops.type_name(value)
        return [(path, Failure("TypeError", f"a number was expected, got {kind}", line))]

    def items(
        self, path: Path, value: Value, line: int
    ) -> list[tuple[Path, tuple[Value, ...] | Failure]]:
        """The elements `for x in value` visits, or the TypeError Python raises.

        Args:
            path: The path.
            value: The iterable.
            line: The source line.

        Returns:
            The elements (or a TypeError for None and numbers) with their path.

        Raises:
            UnsupportedError: For a string whose text is unknown.
        """
        if isinstance(value, SMaybe):
            out: list[tuple[Path, tuple[Value, ...] | Failure]] = []
            for side, is_none in self.fork(path, value.is_none):
                if is_none:
                    out.append((side, Failure("TypeError", "None is not iterable", line)))
                else:
                    out.extend(self.items(side, value.value, line))
            return out
        if isinstance(value, SList):
            return [(path, value.items)]
        if isinstance(value, SDict):
            return [(path, tuple(SStr(key) for key, _ in value.entries))]
        if isinstance(value, SStr):
            return [(path, tuple(SStr(ch) for ch in smt_ops.known_text(value, line)))]
        if isinstance(value, SConcept):
            raise UnsupportedError("iterating over the letters of a concept", line)
        kind = smt_ops.type_name(value)
        return [(path, Failure("TypeError", f"{kind} is not iterable", line))]

    # -- statements ------------------------------------------------------------------------

    def exec_block(self, stmts: Sequence[ast.stmt], path: Path) -> list[FlowResult]:
        """Run statements in order on every path.

        Args:
            stmts: The statements.
            path: The starting path.

        Returns:
            Each finished path with how it finished.
        """
        states: list[FlowResult] = [(path, NORMAL)]
        for stmt in stmts:
            nxt: list[FlowResult] = []
            for current, flow in states:
                nxt.extend(self.exec_stmt(stmt, current) if flow is NORMAL else [(current, flow)])
            states = nxt
        return states

    def exec_stmt(self, stmt: ast.stmt, path: Path) -> list[FlowResult]:
        """Run one statement.

        Args:
            stmt: The statement.
            path: The path.

        Returns:
            Each finished path with how it finished.

        Raises:
            UnsupportedError: For a statement the executor cannot encode.
        """
        handler = getattr(self, f"_s_{type(stmt).__name__}", None)
        if handler is None:
            raise UnsupportedError(f"the statement {type(stmt).__name__}", stmt.lineno)
        flows: list[FlowResult] = handler(stmt, path)
        return flows

    def _s_Expr(self, stmt: ast.Expr, path: Path) -> list[FlowResult]:  # noqa: N802 - dispatch
        if isinstance(stmt.value, ast.Constant):
            return [(path, NORMAL)]
        return [
            (p, v if isinstance(v, Failure) else NORMAL) for p, v in self.eval(stmt.value, path)
        ]

    def _s_Pass(self, stmt: ast.Pass, path: Path) -> list[FlowResult]:  # noqa: N802, ARG002
        return [(path, NORMAL)]

    def _s_Break(self, stmt: ast.Break, path: Path) -> list[FlowResult]:  # noqa: N802, ARG002
        return [(path, Broke())]

    def _s_Continue(self, stmt: ast.Continue, path: Path) -> list[FlowResult]:  # noqa: N802, ARG002
        return [(path, Continued())]

    def _s_Return(self, stmt: ast.Return, path: Path) -> list[FlowResult]:  # noqa: N802
        if stmt.value is None:
            return [(path, Returned(SNone()))]
        return [
            (p, v if isinstance(v, Failure) else Returned(v))
            for p, v in self.eval(stmt.value, path)
        ]

    def _s_Assign(self, stmt: ast.Assign, path: Path) -> list[FlowResult]:  # noqa: N802
        out: list[FlowResult] = []
        for current, value in self.eval(stmt.value, path):
            if isinstance(value, Failure):
                out.append((current, value))
                continue
            states: list[FlowResult] = [(current, NORMAL)]
            for target in stmt.targets:
                states = self._assign_each(states, target, value)
            out.extend(states)
        return out

    def _assign_each(
        self, states: list[FlowResult], target: ast.expr, value: Value
    ) -> list[FlowResult]:
        out: list[FlowResult] = []
        for current, flow in states:
            out.extend(self.assign(target, value, current) if flow is NORMAL else [(current, flow)])
        return out

    def _s_AnnAssign(self, stmt: ast.AnnAssign, path: Path) -> list[FlowResult]:  # noqa: N802
        if stmt.value is None:
            return [(path, NORMAL)]
        return self._s_Assign(
            ast.Assign(targets=[stmt.target], value=stmt.value, lineno=stmt.lineno), path
        )

    def _s_AugAssign(self, stmt: ast.AugAssign, path: Path) -> list[FlowResult]:  # noqa: N802
        target = stmt.target
        load: ast.expr
        if isinstance(target, ast.Name):
            load = ast.Name(id=target.id, ctx=ast.Load(), lineno=stmt.lineno)
        elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            # `x[k] op= v` reads x[k] then stores it: exact when evaluating k twice is harmless.
            if any(isinstance(n, ast.Call | ast.NamedExpr) for n in ast.walk(target.slice)):
                raise UnsupportedError("a call or := inside an augmented subscript", stmt.lineno)
            load = ast.Subscript(
                value=target.value, slice=target.slice, ctx=ast.Load(), lineno=stmt.lineno
            )
        else:
            raise UnsupportedError("augmented assignment to this target", stmt.lineno)
        binop = ast.BinOp(left=load, op=stmt.op, right=stmt.value, lineno=stmt.lineno)
        return self._s_Assign(
            ast.Assign(targets=[stmt.target], value=binop, lineno=stmt.lineno), path
        )

    def _s_If(self, stmt: ast.If, path: Path) -> list[FlowResult]:  # noqa: N802
        out: list[FlowResult] = []
        for current, test in self.eval(stmt.test, path):
            if isinstance(test, Failure):
                out.append((current, test))
                continue
            for side, flag in self.truth(current, test, stmt.lineno):
                if isinstance(flag, Failure):
                    out.append((side, flag))
                else:
                    out.extend(self.exec_block(stmt.body if flag else stmt.orelse, side))
        return out

    def _s_For(self, stmt: ast.For, path: Path) -> list[FlowResult]:  # noqa: N802
        out: list[FlowResult] = []
        for current, iterable in self.eval(stmt.iter, path):
            if isinstance(iterable, Failure):
                out.append((current, iterable))
                continue
            for start, elements in self.items(current, iterable, stmt.lineno):
                if isinstance(elements, Failure):
                    out.append((start, elements))
                else:
                    out.extend(self._loop(stmt, start, elements))
        return out

    def _loop(self, stmt: ast.For, path: Path, elements: Sequence[Value]) -> list[FlowResult]:
        running: list[Path] = [path]
        done: list[FlowResult] = []
        for element in elements:
            nxt: list[Path] = []
            for current in running:
                for bound, flow in self.assign(stmt.target, element, current):
                    if flow is not NORMAL:
                        done.append((bound, flow))
                        continue
                    for after, end in self.exec_block(stmt.body, bound):
                        if isinstance(end, FellThrough | Continued):
                            nxt.append(after)
                        elif isinstance(end, Broke):
                            done.append((after, NORMAL))
                        else:
                            done.append((after, end))
            running = nxt
        for finished in running:
            done.extend(self.exec_block(stmt.orelse, finished))
        return done

    def assign(self, target: ast.expr, value: Value, path: Path) -> list[FlowResult]:
        """Bind `target = value` (a name, a tuple/list to unpack, or a subscript of a name).

        Args:
            target: The assignment target.
            value: The value.
            path: The path.

        Returns:
            The path(s) after the assignment, or a failure (wrong unpack length, missing key).

        Raises:
            UnsupportedError: For targets the executor cannot encode.
        """
        line = getattr(target, "lineno", 0)
        if isinstance(target, ast.Name):
            return [(path.bind(target.id, value), NORMAL)]
        if isinstance(target, ast.Tuple | ast.List):
            return self._unpack(target, value, path, line)
        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            return self._store_item(target, value, path, line)
        raise UnsupportedError("this assignment target", line)

    def _unpack(
        self, target: ast.Tuple | ast.List, value: Value, path: Path, line: int
    ) -> list[FlowResult]:
        if not isinstance(value, SList):
            raise UnsupportedError(f"unpacking {smt_ops.type_name(value)}", line)
        if len(value.items) != len(target.elts):
            return [(path, Failure("ValueError", "wrong number of values to unpack", line))]
        states: list[FlowResult] = [(path, NORMAL)]
        for elt, item in zip(target.elts, value.items, strict=True):
            states = self._assign_each(states, elt, item)
        return states

    def _store_item(
        self, target: ast.Subscript, value: Value, path: Path, line: int
    ) -> list[FlowResult]:
        name = target.value.id  # type: ignore[attr-defined]
        if name not in path.env and name not in path.local_names and name in self._globals:
            # The sandbox runs every sample in one process: module state would carry over.
            raise UnsupportedError("changing a module-level value", line)
        out: list[FlowResult] = []
        for current, key in self.eval(target.slice, path):
            if isinstance(key, Failure):
                out.append((current, key))
                continue
            container = self.lookup(name, current, line)
            if isinstance(container, Failure):
                out.append((current, container))
                continue
            updated = smt_ops.store_item(container, key, value, line)
            out.append(
                (current, updated)
                if isinstance(updated, Failure)
                else (current.bind(name, updated), NORMAL)
            )
        return out

    # -- expressions -----------------------------------------------------------------------

    def eval(self, node: ast.expr, path: Path) -> list[Result]:
        """Evaluate an expression on every path.

        Args:
            node: The expression.
            path: The path.

        Returns:
            Each possible value (or failure) with its path.

        Raises:
            UnsupportedError: For an expression the executor cannot encode.
        """
        handler = getattr(self, f"_e_{type(node).__name__}", None)
        if handler is None:
            raise UnsupportedError(
                f"the expression {type(node).__name__}", getattr(node, "lineno", 0)
            )
        results: list[Result] = handler(node, path)
        return results

    def eval_all(
        self, nodes: Sequence[ast.expr], path: Path
    ) -> list[tuple[Path, tuple[Value, ...] | Failure]]:
        """Evaluate expressions left to right.

        Args:
            nodes: The expressions.
            path: The path.

        Returns:
            Each possible tuple of values (or the first failure) with its path.
        """
        states: list[tuple[Path, tuple[Value, ...] | Failure]] = [(path, ())]
        for node in nodes:
            nxt: list[tuple[Path, tuple[Value, ...] | Failure]] = []
            for current, done in states:
                if isinstance(done, Failure):
                    nxt.append((current, done))
                    continue
                nxt.extend(
                    (p, v if isinstance(v, Failure) else (*done, v))
                    for p, v in self.eval(node, current)
                )
            states = nxt
        return states

    def lookup(self, name: str, path: Path, line: int) -> Value | Failure:
        """A name's value: a local, a module global, a builtin, or a NameError.

        Args:
            name: The name.
            path: The path.
            line: The source line.

        Returns:
            The value, or a NameError failure.
        """
        if name in path.env:
            return path.env[name]
        if name in path.local_names:
            return Failure("NameError", f"`{name}` is used before it is assigned", line)
        if name in self._globals:
            return self._globals[name]
        if name in smt_builtins.BUILTINS:
            return SCallable(name)
        return Failure("NameError", f"`{name}` is not defined", line)

    def _e_Name(self, node: ast.Name, path: Path) -> list[Result]:  # noqa: N802
        return [(path, self.lookup(node.id, path, node.lineno))]

    def _e_Constant(self, node: ast.Constant, path: Path) -> list[Result]:  # noqa: N802
        return [(path, constant(node.value, node.lineno))]

    def _e_List(self, node: ast.List, path: Path) -> list[Result]:  # noqa: N802
        return [
            (p, v if isinstance(v, Failure) else SList(v))
            for p, v in self.eval_all(node.elts, path)
        ]

    def _e_Tuple(self, node: ast.Tuple, path: Path) -> list[Result]:  # noqa: N802
        return [
            (p, v if isinstance(v, Failure) else SList(v, "tuple"))
            for p, v in self.eval_all(node.elts, path)
        ]

    def _e_Dict(self, node: ast.Dict, path: Path) -> list[Result]:  # noqa: N802
        keys: list[str] = []
        for key in node.keys:
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                raise UnsupportedError("a dict key that is not a constant string", node.lineno)
            keys.append(key.value)
        out: list[Result] = []
        for current, values in self.eval_all(node.values, path):
            if isinstance(values, Failure):
                out.append((current, values))
                continue
            result = SDict(())
            for key_name, value in zip(keys, values, strict=True):
                result = result.with_entry(key_name, value)
            out.append((current, result))
        return out

    def _e_JoinedStr(self, node: ast.JoinedStr, path: Path) -> list[Result]:  # noqa: N802
        parts = [v for v in node.values if isinstance(v, ast.FormattedValue)]
        out: list[Result] = []
        for current, values in self.eval_all([p.value for p in parts], path):
            if isinstance(values, Failure):
                out.append((current, values))
                continue
            out.extend(self._format_all(current, parts, values, node.lineno))
        return out

    def _format_all(
        self, path: Path, parts: Sequence[ast.FormattedValue], values: Sequence[Value], line: int
    ) -> list[Result]:
        states: list[Result] = [(path, SStr(None))]
        for part, value in zip(parts, values, strict=True):
            if part.format_spec is None:
                continue
            nxt: list[Result] = []
            for current, done in states:
                nxt.extend(
                    [(current, done)]
                    if isinstance(done, Failure)
                    else self._formatted(current, value, line)
                )
            states = nxt
        return states

    def _formatted(self, path: Path, value: Value, line: int) -> list[Result]:
        """`f"{value:<spec>}"`: numbers and strings format; None, lists and dicts raise."""
        if isinstance(value, SMaybe):
            out: list[Result] = []
            for side, is_none in self.fork(path, value.is_none):
                if is_none:
                    out.append((side, Failure("TypeError", "a format spec on None", line)))
                else:
                    out.extend(self._formatted(side, value.value, line))
            return out
        if isinstance(value, SNone | SList | SDict):
            kind = smt_ops.type_name(value)
            return [(path, Failure("TypeError", f"a format spec on {kind}", line))]
        return [(path, SStr(None))]

    def _e_NamedExpr(self, node: ast.NamedExpr, path: Path) -> list[Result]:  # noqa: N802
        return [
            (p if isinstance(v, Failure) else p.bind(node.target.id, v), v)
            for p, v in self.eval(node.value, path)
        ]

    def _e_Attribute(self, node: ast.Attribute, path: Path) -> list[Result]:  # noqa: N802
        receiver_var = node.value.id if isinstance(node.value, ast.Name) else None
        out: list[Result] = []
        for current, base in self.eval(node.value, path):
            if isinstance(base, Failure):
                out.append((current, base))
            elif isinstance(base, SModule):
                out.append((current, smt_builtins.math_attribute(node.attr, node.lineno)))
            else:
                out.extend(self._method_of(current, base, node.attr, receiver_var, node.lineno))
        return out

    def _method_of(
        self, path: Path, base: Value, attr: str, receiver_var: str | None, line: int
    ) -> list[Result]:
        """`base.attr`: a bound method, or the AttributeError Python raises at lookup time."""
        if isinstance(base, SMaybe):
            out: list[Result] = []
            for side, is_none in self.fork(path, base.is_none):
                if is_none:
                    out.append((side, Failure("AttributeError", f"None has no .{attr}", line)))
                else:
                    out.extend(self._method_of(side, base.value, attr, receiver_var, line))
            return out
        problem = smt_builtins.method_problem(base, attr, line)
        if problem is not None:
            return [(path, problem)]
        return [(path, SCallable(f"method.{attr}", base, receiver_var))]

    def _e_Subscript(self, node: ast.Subscript, path: Path) -> list[Result]:  # noqa: N802
        if isinstance(node.slice, ast.Slice):
            return self._slice(node, node.slice, path)
        out: list[Result] = []
        for current, pair in self.eval_all([node.value, node.slice], path):
            if isinstance(pair, Failure):
                out.append((current, pair))
            else:
                out.extend(smt_ops.subscript(self, current, pair[0], pair[1], node.lineno))
        return out

    def _slice(self, node: ast.Subscript, piece: ast.Slice, path: Path) -> list[Result]:
        bounds = tuple(
            _constant_int(b, node.lineno) for b in (piece.lower, piece.upper, piece.step)
        )
        out: list[Result] = []
        for current, base in self.eval(node.value, path):
            if isinstance(base, Failure):
                out.append((current, base))
            else:
                out.append((current, smt_ops.slice_list(base, bounds, node.lineno)))  # type: ignore[arg-type]
        return out

    def _e_BinOp(self, node: ast.BinOp, path: Path) -> list[Result]:  # noqa: N802
        out: list[Result] = []
        for current, pair in self.eval_all([node.left, node.right], path):
            if isinstance(pair, Failure):
                out.append((current, pair))
            else:
                out.extend(
                    smt_ops.binary(self, current, node.op, pair[0], pair[1], line=node.lineno)
                )
        return out

    def _e_UnaryOp(self, node: ast.UnaryOp, path: Path) -> list[Result]:  # noqa: N802
        out: list[Result] = []
        for current, operand in self.eval(node.operand, path):
            if isinstance(operand, Failure):
                out.append((current, operand))
            else:
                out.extend(smt_ops.unary(self, current, node.op, operand, node.lineno))
        return out

    def _e_BoolOp(self, node: ast.BoolOp, path: Path) -> list[Result]:  # noqa: N802
        stop_on = isinstance(node.op, ast.Or)
        pending: list[Path] = [path]
        out: list[Result] = []
        for index, operand in enumerate(node.values):
            last = index == len(node.values) - 1
            nxt: list[Path] = []
            for current in pending:
                for evaluated, value in self.eval(operand, current):
                    if isinstance(value, Failure) or last:
                        out.append((evaluated, value))
                        continue
                    for side, flag in self.truth(evaluated, value, node.lineno):
                        if isinstance(flag, Failure) or flag == stop_on:
                            out.append((side, flag if isinstance(flag, Failure) else value))
                        else:
                            nxt.append(side)
            pending = nxt
        return out

    def _e_IfExp(self, node: ast.IfExp, path: Path) -> list[Result]:  # noqa: N802
        out: list[Result] = []
        for current, test in self.eval(node.test, path):
            if isinstance(test, Failure):
                out.append((current, test))
                continue
            for side, flag in self.truth(current, test, node.lineno):
                if isinstance(flag, Failure):
                    out.append((side, flag))
                else:
                    out.extend(self.eval(node.body if flag else node.orelse, side))
        return out

    def _e_Compare(self, node: ast.Compare, path: Path) -> list[Result]:  # noqa: N802
        out: list[Result] = []
        for current, left in self.eval(node.left, path):
            if isinstance(left, Failure):
                out.append((current, left))
            else:
                out.extend(
                    self._compare_chain(current, left, node.ops, node.comparators, node.lineno)
                )
        return out

    def _compare_chain(
        self,
        path: Path,
        left: Value,
        ops: Sequence[ast.cmpop],
        rights: Sequence[ast.expr],
        line: int,
    ) -> list[Result]:
        out: list[Result] = []
        for current, right in self.eval(rights[0], path):
            if isinstance(right, Failure):
                out.append((current, right))
                continue
            for compared, result in smt_ops.compare(self, current, ops[0], left, right, line=line):
                if isinstance(result, Failure) or len(ops) == 1:
                    out.append((compared, result))
                    continue
                for side, flag in self.truth(compared, result, line):
                    if isinstance(flag, Failure) or not flag:
                        out.append(
                            (side, flag if isinstance(flag, Failure) else SBool(z3.BoolVal(False)))
                        )
                    else:
                        out.extend(self._compare_chain(side, right, ops[1:], rights[1:], line))
        return out

    def _e_ListComp(self, node: ast.ListComp, path: Path) -> list[Result]:  # noqa: N802
        return self.comprehension(node, path)

    def _e_GeneratorExp(self, node: ast.GeneratorExp, path: Path) -> list[Result]:  # noqa: N802
        return self.comprehension(node, path)

    def comprehension(self, node: ast.ListComp | ast.GeneratorExp, path: Path) -> list[Result]:
        """Evaluate a list comprehension or generator to a list of its elements.

        Args:
            node: The comprehension.
            path: The path.

        Returns:
            Each possible list (or failure) with its path.
        """

        def collect(p: Path, acc: object, value: Value) -> list[tuple[Path, object, bool]]:
            assert isinstance(acc, tuple)  # noqa: S101 - internal invariant
            return [(p, (*acc, value), False)]

        out: list[Result] = []
        for current, acc in self.fold(node, path, (), collect):
            out.append((current, acc if isinstance(acc, Failure) else SList(acc)))  # type: ignore[arg-type]
        return out

    def fold(
        self,
        node: ast.ListComp | ast.GeneratorExp,
        path: Path,
        start: object,
        step: Callable[[Path, object, Value], list[tuple[Path, object, bool]]],
    ) -> list[tuple[Path, object]]:
        """Fold a comprehension's elements in order; `step` may stop early (any/all).

        Comprehension variables are restored afterwards: they live in their own scope.

        Args:
            node: The comprehension.
            path: The path.
            start: The initial accumulator.
            step: `(path, acc, element) -> [(path, acc, stop)]`.

        Returns:
            Each possible final accumulator (or a Failure) with its path.
        """
        names = {
            n.id for g in node.generators for n in ast.walk(g.target) if isinstance(n, ast.Name)
        }
        states = self._generators(node, 0, [(path, start, False)], step)
        return [(_restore(p, path, names), acc) for p, acc, _ in states]

    def _generators(
        self,
        node: ast.ListComp | ast.GeneratorExp,
        index: int,
        states: list[tuple[Path, object, bool]],
        step: Callable[[Path, object, Value], list[tuple[Path, object, bool]]],
    ) -> list[tuple[Path, object, bool]]:
        gen = node.generators[index]
        if gen.is_async:
            raise UnsupportedError("an async comprehension", node.lineno)
        out: list[tuple[Path, object, bool]] = []
        for current, acc, stopped in states:
            if stopped or isinstance(acc, Failure):
                out.append((current, acc, stopped))
                continue
            for evaluated, iterable in self.eval(gen.iter, current):
                if isinstance(iterable, Failure):
                    out.append((evaluated, iterable, True))
                    continue
                for start, elements in self.items(evaluated, iterable, node.lineno):
                    if isinstance(elements, Failure):
                        out.append((start, elements, True))
                    else:
                        out.extend(self._elements(node, index, start, acc, elements, step=step))
        return out

    def _elements(
        self,
        node: ast.ListComp | ast.GeneratorExp,
        index: int,
        path: Path,
        acc: object,
        elements: Sequence[Value],
        *,
        step: Callable[[Path, object, Value], list[tuple[Path, object, bool]]],
    ) -> list[tuple[Path, object, bool]]:
        gen = node.generators[index]
        states: list[tuple[Path, object, bool]] = [(path, acc, False)]
        for element in elements:
            nxt: list[tuple[Path, object, bool]] = []
            for current, value, stopped in states:
                if stopped or isinstance(value, Failure):
                    nxt.append((current, value, stopped))
                    continue
                for bound, flow in self.assign(gen.target, element, current):
                    if isinstance(flow, Failure):
                        nxt.append((bound, flow, True))
                    else:
                        nxt.extend(self._filtered(node, index, bound, value, gen.ifs, step=step))
            states = nxt
        return states

    def _filtered(
        self,
        node: ast.ListComp | ast.GeneratorExp,
        index: int,
        path: Path,
        acc: object,
        conditions: Sequence[ast.expr],
        *,
        step: Callable[[Path, object, Value], list[tuple[Path, object, bool]]],
    ) -> list[tuple[Path, object, bool]]:
        if conditions:
            out: list[tuple[Path, object, bool]] = []
            for current, test in self.eval(conditions[0], path):
                if isinstance(test, Failure):
                    out.append((current, test, True))
                    continue
                for side, flag in self.truth(current, test, node.lineno):
                    if isinstance(flag, Failure):
                        out.append((side, flag, True))
                    elif flag:
                        out.extend(
                            self._filtered(node, index, side, acc, conditions[1:], step=step)
                        )
                    else:
                        out.append((side, acc, False))
            return out
        if index + 1 < len(node.generators):
            return self._generators(node, index + 1, [(path, acc, False)], step)
        out = []
        for current, value in self.eval(node.elt, path):
            out.extend(
                [(current, value, True)]
                if isinstance(value, Failure)
                else step(current, acc, value)
            )
        return out

    def _e_Call(self, node: ast.Call, path: Path) -> list[Result]:  # noqa: N802
        if any(k.arg is None for k in node.keywords):
            raise UnsupportedError("** arguments", node.lineno)
        out: list[Result] = []
        for current, callee in self.eval(node.func, path):
            if isinstance(callee, Failure):
                out.append((current, callee))
            elif not isinstance(callee, SCallable):
                out.append(
                    (
                        current,
                        Failure(
                            "TypeError", f"{smt_ops.type_name(callee)} is not callable", node.lineno
                        ),
                    )
                )
            else:
                out.extend(self._call(node, callee, current))
        return out

    def _call(self, node: ast.Call, callee: SCallable, path: Path) -> list[Result]:
        lazy = (
            callee.name in {"any", "all"}
            and len(node.args) == 1
            and isinstance(node.args[0], ast.GeneratorExp)
        )
        if lazy and not node.keywords:
            return smt_builtins.lazy_any_all(self, path, callee.name, node.args[0], node.lineno)  # type: ignore[arg-type]
        nodes = [*node.args, *(k.value for k in node.keywords)]
        names = [k.arg for k in node.keywords if k.arg is not None]  # `**` was refused above
        out: list[Result] = []
        for current, values in self.eval_all(nodes, path):
            if isinstance(values, Failure):
                out.append((current, values))
                continue
            positional = values[: len(node.args)]
            keywords = dict(zip(names, values[len(node.args) :], strict=True))
            out.extend(
                smt_builtins.call(self, current, callee, positional, keywords, line=node.lineno)
            )
        return out

    # -- the result ------------------------------------------------------------------------

    def result_value(self, path: Path, value: Value) -> list[tuple[Path, Any | Failure]]:
        """Check that `value` is a valid `CheckResult` and return its `value` as a Z3 Bool.

        Args:
            path: The path.
            value: What `check` returned.

        Returns:
            The result's truth value (or why the result is invalid) with its path.
        """
        line = self.program.functions[ENTRY].lineno
        if not isinstance(value, SDict):
            return [(path, Failure("InvalidResult", "check must return a dict", line))]
        extra = {k for k, _ in value.entries} - _RESULT_KEYS
        if extra:
            return [(path, Failure("InvalidResult", f"unexpected keys {sorted(extra)}", line))]
        verdict = value.lookup("value")
        if verdict is None:
            return [(path, Failure("InvalidResult", "the result has no `value`", line))]
        states = self._field_ok(path, value.lookup("measure"), _measure_ok, "measure", line)
        states = [
            (p, v)
            for s, f in states
            for p, v in (
                [(s, f)]
                if isinstance(f, Failure)
                else self._field_ok(s, value.lookup("why"), _why_ok, "why", line)
            )
        ]
        out: list[tuple[Path, Any | Failure]] = []
        for current, failure in states:
            out.extend(
                [(current, failure)]
                if isinstance(failure, Failure)
                else self._verdict(current, verdict, line)
            )
        return out

    def _verdict(self, path: Path, verdict: Value, line: int) -> list[tuple[Path, Any | Failure]]:
        if isinstance(verdict, SBool):
            return [(path, verdict.expr)]
        if isinstance(verdict, SMaybe) and isinstance(verdict.value, SBool):
            return [
                (
                    side,
                    Failure("InvalidResult", "`value` is None", line)
                    if is_none
                    else verdict.value.expr,
                )
                for side, is_none in self.fork(path, verdict.is_none)
            ]
        return [
            (
                path,
                Failure(
                    "InvalidResult",
                    f"`value` must be a bool, not {smt_ops.type_name(verdict)}",
                    line,
                ),
            )
        ]

    def _field_ok(
        self, path: Path, value: Value | None, accept: Callable[[Value], bool], name: str, line: int
    ) -> list[tuple[Path, Failure | None]]:
        if value is None or accept(value):
            return [(path, None)]
        if isinstance(value, SMaybe) and accept(value.value):
            return [(path, None)]
        if isinstance(value, SMaybe):
            return [
                (side, None if is_none else Failure("InvalidResult", f"bad `{name}`", line))
                for side, is_none in self.fork(path, value.is_none)
            ]
        return [
            (
                path,
                Failure(
                    "InvalidResult",
                    f"`{name}` has the wrong type ({smt_ops.type_name(value)})",
                    line,
                ),
            )
        ]


def _measure_ok(value: Value) -> bool:
    return isinstance(value, SNum | SNone)


def _why_ok(value: Value) -> bool:
    if isinstance(value, SConcept):
        return all(len(name) <= MAX_WHY_CHARS for name in value.names)
    return isinstance(value, SStr) and (value.text is None or len(value.text) <= MAX_WHY_CHARS)


def _restore(path: Path, before: Path, names: set[str]) -> Path:
    env = {k: v for k, v in path.env.items() if k not in names}
    env.update({k: before.env[k] for k in names if k in before.env})
    return replace(path, env=env)


def _constant_int(node: ast.expr | None, line: int) -> int | None:
    if node is None:
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _constant_int(node.operand, line)
        return None if inner is None else -inner
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return int(node.value)
    raise UnsupportedError("a slice with non-constant bounds", line)


def constant(raw: object, line: int) -> Value:
    """A Python constant as a symbolic value.

    Args:
        raw: The constant.
        line: The source line.

    Returns:
        The value.

    Raises:
        UnsupportedError: For bytes, complex numbers, NaN or infinity.
    """
    if raw is None:
        return SNone()
    if isinstance(raw, bool):
        return SBool(z3.BoolVal(raw))
    if isinstance(raw, int):
        return SNum(z3.RealVal(raw), "int")
    if isinstance(raw, float):
        if raw != raw or raw in {float("inf"), float("-inf")}:  # noqa: PLR0124 - NaN check
            raise UnsupportedError("NaN or infinity", line)
        exact = Fraction(repr(raw))
        return SNum(z3.Q(exact.numerator, exact.denominator), "float")
    if isinstance(raw, str):
        return SStr(raw)
    raise UnsupportedError(f"the constant {raw!r}", line)
