"""Validate and run a model-written rule check: an allow-list validator plus a locked-down child.

A rule extension is one small Python module that Muse Code writes when the typed Rule IR cannot
express an owner's request (`rulecam.extensions`). It must define `def check(sample) -> dict`,
where `sample` is one `schema.CheckSample` as a plain dict and the answer validates as
`schema.CheckResult` (`{"value": bool, "measure": float | None, "why": str}`). Nothing here
trusts that code.

**Layer 1, the validator (`validate`), before anything runs.** It parses the source and accepts
only an allow-list of syntax:

- top level: `import math`, constant assignments, a docstring and plain `def` functions, one of
  them `check` with exactly one positional parameter; no decorators, defaults, `*args` or
  `**kwargs`;
- statements: assignment, `if`, `for`, `while`, `return`, `pass`, `break`, `continue`, and
  expression statements; no `try`, `with`, `raise`, `class`, `lambda`, nested `def`, `global`,
  `nonlocal`, `del`, `assert`, `match`, `yield`, `await` or `async`;
- names: no name starting with `_`, no name outside the safe builtins unless the module binds
  it, and an explicit deny-list (`eval`, `exec`, `open`, `getattr`, `type`, …) for clear errors;
- attributes: an allow-list only (`math.sqrt`, `dict.get`, `list.append`, `str.lower`, …), so
  `().__class__`, `frame.f_globals`, `str.format` traversal and every other attribute are refused;
- size: at most `MAX_LINES` lines, `MAX_CHARS` characters and `MAX_NODES` syntax nodes.

**Layer 2, the child process (`run_check`).** Accepted code runs in `python -I -S _harness.py`:
isolated mode (no `PYTHONPATH`, user site, or script directory on `sys.path`), no site packages
(so no Rulecam, numpy or anything installed), an empty environment (no secrets), an empty
temporary working directory, and `setrlimit` caps on CPU seconds, address space, file size (0),
open files and processes, applied before the code is compiled. The code gets a restricted
`__builtins__` (the same safe names the validator allows) and an importer that only returns
`math`. The parent kills the child after `wall_s`, caps the bytes it reads back, and validates
every answer against `schema.CheckResult`. All samples of one window go to one child.

**What this does not guarantee.** It is defence in depth inside one machine, not a virtual
machine. There is no seccomp filter, no network or mount namespace and no separate user: code
that escaped both layers (for example through a CPython bug) would run as the API's user and
could reach its files and the network. `RLIMIT_NPROC` is ignored for root, so run the API as an
unprivileged user. For untrusted multi-tenant use, run the child under gVisor, nsjail or a WASM
runtime as well. It does not judge whether the check is *correct*: that is `extensions.proof`
(tests and mutants) and `extensions.review` (a second model), and an owner approves it.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from rulecam.extensions.errors import ExtensionRejectedError, SandboxError
from rulecam.extensions.schema import CheckResult

HARNESS: Final = Path(__file__).with_name("_harness.py")
MAX_LINES: Final = 200
MAX_CHARS: Final = 12_000
MAX_NODES: Final = 3_000
ENTRY: Final = "check"

SAFE_BUILTINS: Final = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "float",
        "int",
        "isinstance",
        "len",
        "list",
        "max",
        "min",
        "range",
        "round",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
        "True",
        "False",
        "None",
        "ArithmeticError",
        "Exception",
        "IndexError",
        "KeyError",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
    }
)
DENIED_NAMES: Final = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "input",
        "breakpoint",
        "help",
        "type",
        "object",
        "super",
        "memoryview",
        "bytearray",
        "classmethod",
        "staticmethod",
        "property",
        "exit",
        "quit",
        "print",
        "id",
        "hash",
        "iter",
        "next",
        "callable",
        "issubclass",
        "format",
        "repr",
        "chr",
        "ord",
        "os",
        "sys",
        "subprocess",
        "builtins",
        "importlib",
    }
)
MATH_ATTRIBUTES: Final = frozenset(
    {
        "acos",
        "asin",
        "atan",
        "atan2",
        "ceil",
        "cos",
        "degrees",
        "dist",
        "e",
        "exp",
        "fabs",
        "floor",
        "hypot",
        "inf",
        "isclose",
        "isfinite",
        "isinf",
        "isnan",
        "log",
        "log10",
        "nan",
        "pi",
        "radians",
        "sin",
        "sqrt",
        "tan",
        "trunc",
    }
)
DATA_METHODS: Final = frozenset(
    {
        "append",
        "count",
        "endswith",
        "extend",
        "get",
        "index",
        "items",
        "join",
        "keys",
        "lower",
        "split",
        "startswith",
        "strip",
        "upper",
        "values",
    }
)
_STATEMENTS: Final = (
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.If,
    ast.For,
    ast.While,
    ast.Return,
    ast.Pass,
    ast.Break,
    ast.Continue,
    ast.Expr,
)
_EXPRESSIONS: Final = (
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.IfExp,
    ast.Dict,
    ast.Set,
    ast.List,
    ast.Tuple,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Compare,
    ast.Call,
    ast.Constant,
    ast.Attribute,
    ast.Subscript,
    ast.Slice,
    ast.Name,
    ast.NamedExpr,
    ast.JoinedStr,
    ast.FormattedValue,
)
_SUPPORT: Final = (
    ast.Module,
    ast.FunctionDef,
    ast.Import,
    ast.alias,
    ast.arguments,
    ast.arg,
    ast.keyword,
    ast.comprehension,
    ast.expr_context,
    ast.operator,
    ast.boolop,
    ast.unaryop,
    ast.cmpop,
)


@dataclass(frozen=True)
class SampleOutcome:
    """One sample's outcome: a valid result, or why there is none.

    Attributes:
        result: The validated answer, or None when the check failed on this sample.
        error: Why there is no result (the exception's type and message, or a bad shape).
    """

    result: CheckResult | None
    error: str | None = None


@dataclass(frozen=True)
class SandboxLimits:
    """Limits for one child process (one batch of samples)."""

    cpu_s: int = 2
    memory_mb: int = 256
    max_files: int = 8
    wall_s: float = 5.0
    max_output_bytes: int = 1_000_000
    max_samples: int = 5_000


DEFAULT_LIMITS: Final = SandboxLimits()


def validate(code: str) -> None:
    """Accept `code` or raise with every broken allow-list rule.

    Args:
        code: The module source.

    Raises:
        ExtensionRejectedError: With one short reason per problem, if any rule is broken.
    """
    problems = find_problems(code)
    if problems:
        raise ExtensionRejectedError(problems)


def find_problems(code: str) -> list[str]:
    """Every allow-list rule `code` breaks, in source order (empty when it is accepted).

    Args:
        code: The module source.

    Returns:
        One short reason per problem.
    """
    problems = _size_problems(code)
    if problems:
        return problems
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return [f"syntax error on line {exc.lineno}: {exc.msg}"]
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        return [f"too many syntax nodes ({len(nodes)} > {MAX_NODES})"]
    problems.extend(_top_level_problems(tree))
    problems.extend(_node_problems(nodes, _bound_names(nodes)))
    return problems


def _size_problems(code: str) -> list[str]:
    lines = code.count("\n") + 1
    if lines > MAX_LINES:
        return [f"too long: {lines} lines (max {MAX_LINES})"]
    if len(code) > MAX_CHARS:
        return [f"too long: {len(code)} characters (max {MAX_CHARS})"]
    return []


def _top_level_problems(tree: ast.Module) -> list[str]:
    problems: list[str] = []
    checks = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == ENTRY]
    if len(checks) != 1:
        problems.append(f"define exactly one top-level `def {ENTRY}(sample)`")
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(a.name != "math" or a.asname not in {None, "math"} for a in node.names):
                problems.append(f"line {node.lineno}: only `import math` is allowed")
        elif isinstance(node, ast.FunctionDef):
            problems.extend(_function_problems(node))
        elif not _constant_or_docstring(node):
            problems.append(
                f"line {node.lineno}: {type(node).__name__} is not allowed at top level"
            )
    return problems


def _constant_or_docstring(node: ast.stmt) -> bool:
    if isinstance(node, ast.Assign | ast.AnnAssign):
        return True
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


def _function_problems(node: ast.FunctionDef) -> list[str]:
    problems: list[str] = []
    args = node.args
    if node.decorator_list:
        problems.append(f"line {node.lineno}: decorators are not allowed")
    if args.vararg or args.kwarg or args.kwonlyargs or args.posonlyargs:
        problems.append(f"line {node.lineno}: only plain positional parameters are allowed")
    if args.defaults or args.kw_defaults:
        problems.append(f"line {node.lineno}: default parameter values are not allowed")
    if node.name == ENTRY and len(args.args) != 1:
        problems.append(f"line {node.lineno}: `{ENTRY}` takes exactly one parameter, `sample`")
    for inner in ast.walk(node):
        if inner is not node and isinstance(inner, ast.FunctionDef):
            problems.append(f"line {inner.lineno}: nested functions are not allowed")
        elif isinstance(inner, ast.Import):
            problems.append(f"line {inner.lineno}: imports belong at the top level")
    return problems


def _bound_names(nodes: Sequence[ast.AST]) -> frozenset[str]:
    """Every name the module binds anywhere (over-approximates scopes, which is safe here)."""
    bound: set[str] = {"math"}
    for node in nodes:
        if isinstance(node, ast.FunctionDef):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    return frozenset(bound)


def _node_problems(nodes: Sequence[ast.AST], bound: frozenset[str]) -> list[str]:
    problems: list[str] = []
    for node in nodes:
        line = getattr(node, "lineno", 0)
        if not isinstance(node, _STATEMENTS + _EXPRESSIONS + _SUPPORT):
            problems.append(f"line {line}: {type(node).__name__} is not allowed")
        elif isinstance(node, ast.Name):
            problems.extend(_name_problem(node, bound))
        elif isinstance(node, ast.Attribute):
            problems.extend(_attribute_problem(node))
        elif isinstance(node, ast.arg | ast.FunctionDef) and _private(_bound_label(node)):
            problems.append(f"line {line}: names starting with '_' are not allowed")
    return problems


def _bound_label(node: ast.arg | ast.FunctionDef) -> str:
    return node.arg if isinstance(node, ast.arg) else node.name


def _private(name: str) -> bool:
    return name.startswith("_")


def _name_problem(node: ast.Name, bound: frozenset[str]) -> list[str]:
    name = node.id
    if _private(name):
        return [f"line {node.lineno}: names starting with '_' are not allowed ({name})"]
    if name in DENIED_NAMES:
        return [f"line {node.lineno}: `{name}` is not allowed"]
    if isinstance(node.ctx, ast.Load) and name not in bound and name not in SAFE_BUILTINS:
        return [f"line {node.lineno}: unknown name `{name}`"]
    return []


def _attribute_problem(node: ast.Attribute) -> list[str]:
    attr = node.attr
    if _private(attr):
        return [f"line {node.lineno}: attributes starting with '_' are not allowed ({attr})"]
    on_math = isinstance(node.value, ast.Name) and node.value.id == "math"
    allowed = MATH_ATTRIBUTES if on_math else DATA_METHODS
    if attr not in allowed:
        return [f"line {node.lineno}: attribute `.{attr}` is not allowed"]
    if not on_math and not isinstance(node.ctx, ast.Load):
        return [f"line {node.lineno}: assigning to attributes is not allowed"]
    return []


def run_check(
    code: str,
    samples: Sequence[Mapping[str, object]],
    *,
    limits: SandboxLimits | None = None,
) -> list[SampleOutcome]:
    """Validate `code`, then run its `check` on every sample in one locked-down child process.

    Keyword-only options keep room for more (e.g. approved helper calls) without breaking
    callers.

    Args:
        code: The module source.
        samples: JSON-serialisable samples (`schema.CheckSample.as_json`).
        limits: Process limits for the batch (`DEFAULT_LIMITS` when None).

    Returns:
        One outcome per sample, in order.

    Raises:
        ExtensionRejectedError: If the code fails validation (it is never run).
        SandboxError: If the child is killed, times out, or answers anything but valid JSON.
    """
    limits = limits or DEFAULT_LIMITS
    validate(code)
    if len(samples) > limits.max_samples:
        msg = f"too many samples in one batch ({len(samples)} > {limits.max_samples})"
        raise SandboxError(msg)
    if not samples:
        return []
    answer = _run_child(code, samples, limits)
    return _parse_answer(answer, len(samples))


def _run_child(
    code: str, samples: Sequence[Mapping[str, object]], limits: SandboxLimits
) -> dict[str, object]:
    payload = json.dumps(
        {
            "code": code,
            "samples": list(samples),
            "limits": {
                "cpu_s": limits.cpu_s,
                "memory_mb": limits.memory_mb,
                "max_files": limits.max_files,
                "max_output_bytes": limits.max_output_bytes,
            },
        },
        allow_nan=False,
    )
    with tempfile.TemporaryDirectory(prefix="rulecam-ext-") as cwd:
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, our own harness, no shell
                [sys.executable, "-I", "-S", str(HARNESS)],
                input=payload.encode("ascii"),
                capture_output=True,
                env={},
                cwd=cwd,
                timeout=limits.wall_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"the check ran longer than {limits.wall_s:g} s and was killed"
            raise SandboxError(msg) from exc
    if done.returncode != 0:
        msg = f"the check's process ended with code {done.returncode} (killed by a limit?)"
        raise SandboxError(msg)
    if len(done.stdout) > limits.max_output_bytes:
        msg = f"the check wrote too much ({len(done.stdout)} bytes)"
        raise SandboxError(msg)
    try:
        answer = json.loads(done.stdout.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "the check's process did not answer valid JSON"
        raise SandboxError(msg) from exc
    if not isinstance(answer, dict):
        msg = "the check's process did not answer a JSON object"
        raise SandboxError(msg)
    return answer


def _parse_answer(answer: Mapping[str, object], expected: int) -> list[SampleOutcome]:
    fatal = answer.get("fatal")
    if fatal is not None:
        msg = f"the check failed as a whole: {str(fatal)[:200]}"
        raise SandboxError(msg)
    results = answer.get("results")
    if not isinstance(results, list) or len(results) != expected:
        msg = "the check's process answered the wrong number of results"
        raise SandboxError(msg)
    return [_outcome(entry) for entry in results]


def _outcome(entry: object) -> SampleOutcome:
    if not isinstance(entry, dict) or not isinstance(entry.get("ok"), bool):
        return SampleOutcome(result=None, error="malformed result entry")
    if not entry["ok"]:
        return SampleOutcome(result=None, error=str(entry.get("error", "error"))[:200])
    try:
        return SampleOutcome(result=CheckResult.model_validate(entry.get("result")))
    except ValidationError as exc:
        reason = exc.errors()[0]
        where = ".".join(str(p) for p in reason.get("loc", ())) or "result"
        return SampleOutcome(result=None, error=f"bad result ({where}: {reason.get('msg')})")
