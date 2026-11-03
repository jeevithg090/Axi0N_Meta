"""Prove a generated check before anyone may approve it: a list of `ProofStep`s.

`prove(ctx)` runs each step on a `ProofContext` and collects a `StepResult` per step into a
`ProofReport`. The default steps:

1. `validator`: the sandbox's allow-list (`sandbox.validate`).
2. `muse_code_tests`: the generator's own cases from `test_check.py`. That file is parsed as data
   with `ast.literal_eval` (`parse_cases`), never executed; the cases run in the sandbox. At least
   `MIN_CASES` are required.
3. `boundary`: our cases, independent of the generator. With a reference line: an object just
   across the line on each side (5 px), far on each side, 3 px either side of a steep sloped
   line (catches checks that measure at the wrong x), two objects with only one across (in both
   orders), no objects, and the line missing. When the spec says above or below, each case
   has an expected value; otherwise pairs must differ across the line and agree on each side.
   Robustness samples (empty, uncalibrated) must run without an error.
4. `mutation`: code-level mutants of the check (flip `<`/`>`, `<=`/`>=`, `==`/`!=`; numbers ±10%,
   except the digits of `round(x, n)`, which only change how a number is shown;
   `and`↔`or`; drop one operand of an `and`/`or`; replace an `if` test with `True`; negate the
   returned `value`). Every case from steps 2 and 3 runs against each mutant; a mutant is killed
   when any expectation breaks or it errors. At least `KILL_THRESHOLD` must die, or the tests are
   too weak to trust and the extension is rejected.
5. `muse_image_scenes`: rendered scenes need live Muse Image and SAM 3.1 and are not run here;
   the step says so (skipped), and a skip never counts as a pass.

The report passes only when every required step passed. Add-ons register more steps by passing
their own list to `prove` (`DEFAULT_STEPS + (MyStep(),)`); a step is any object with a `name`
and `run(ctx) -> StepResult`.
"""

from __future__ import annotations

import ast
import copy
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from rulecam.extensions.errors import ExtensionError, ExtensionRejectedError
from rulecam.extensions.generate import CheckSpec
from rulecam.extensions.sandbox import SampleOutcome, run_check, validate

MIN_CASES: Final = 3
MAX_CASES: Final = 40
MAX_TESTS_CHARS: Final = 60_000
MAX_MUTANTS: Final = 60
KILL_THRESHOLD: Final = 0.8
BOUNDARY_PX: Final = 5.0
REQUIRED_STEPS: Final = ("validator", "muse_code_tests", "boundary", "mutation")

Sample = dict[str, object]
Runner = Callable[[str, list[Sample]], list[SampleOutcome]]
Status = Literal["passed", "failed", "skipped"]


@dataclass(frozen=True)
class ProofCase:
    """One sample with the value the check must give.

    Attributes:
        name: Short case name.
        sample: The sample dict.
        expect: The required `value`, or None when only "runs without an error" is required.
        source: Who wrote it: the generator, or Rulecam's boundary or robustness cases.
    """

    name: str
    sample: Sample
    expect: bool | None
    source: Literal["generator", "boundary", "robustness"]


@dataclass(frozen=True)
class ProofPair:
    """Two samples whose values must be equal (`same`) or must differ."""

    name: str
    first: Sample
    second: Sample
    same: bool


@dataclass(frozen=True)
class ProofContext:
    """Everything a step may read.

    Attributes:
        code: The generated `check.py`.
        spec: What the owner asked for.
        generator_cases: Cases parsed from the generator's `test_check.py`.
        case_problems: Why some generator cases were dropped while parsing.
        runner: Runs `code` over samples (the sandbox by default).
    """

    code: str
    spec: CheckSpec
    generator_cases: tuple[ProofCase, ...] = ()
    case_problems: tuple[str, ...] = ()
    runner: Runner = field(default=run_check)


class CaseResult(BaseModel):
    """One case's outcome in a step."""

    model_config = ConfigDict(frozen=True)

    name: str
    source: str
    expect: bool | None
    got: bool | None
    error: str | None = None
    passed: bool


class StepResult(BaseModel):
    """One proof step's outcome.

    Attributes:
        name: The step's name.
        status: passed, failed, or skipped (never counts as passed).
        detail: One line in plain words.
        metrics: Numbers behind it (e.g. `killed`, `total`, `score`).
        cases: Per-case outcomes, when the step ran cases.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    status: Status
    detail: str
    metrics: dict[str, float] = Field(default_factory=dict)
    cases: tuple[CaseResult, ...] = ()


class ProofReport(BaseModel):
    """Every step's result, and whether the extension may be put up for approval."""

    model_config = ConfigDict(frozen=True)

    steps: tuple[StepResult, ...]
    passed: bool


class ProofStep(Protocol):
    """One proof step; add-ons implement this to add fuzzing, metamorphic tests and more.

    `name` may be a plain or read-only attribute (a frozen dataclass field works).
    """

    @property
    def name(self) -> str:
        """The step's name in the report."""
        ...

    def run(self, ctx: ProofContext) -> StepResult:
        """Run the step on `ctx`."""
        ...


def parse_cases(tests: str) -> tuple[list[ProofCase], list[str]]:
    """Read `CASES = [...]` from a generator's test file as data, never running it.

    Args:
        tests: The text of `test_check.py`.

    Returns:
        The valid cases (at most `MAX_CASES`) and why any were dropped.
    """
    if len(tests) > MAX_TESTS_CHARS:
        return [], [f"test file too long ({len(tests)} characters)"]
    try:
        tree = ast.parse(tests)
        literal = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "CASES" for t in node.targets)
        )
        raw = ast.literal_eval(literal)
    except StopIteration:
        return [], ["no `CASES = [...]` in the test file"]
    except (SyntaxError, ValueError, TypeError, RecursionError, MemoryError) as exc:
        return [], [f"test file is not a literal: {type(exc).__name__}"]
    if not isinstance(raw, list):
        return [], ["CASES is not a list"]
    cases: list[ProofCase] = []
    problems: list[str] = []
    for index, entry in enumerate(raw[:MAX_CASES]):
        case = _case(index, entry)
        if isinstance(case, str):
            problems.append(case)
        else:
            cases.append(case)
    if len(raw) > MAX_CASES:
        problems.append(f"only the first {MAX_CASES} cases are used")
    return cases, problems


def _case(index: int, entry: object) -> ProofCase | str:
    if not isinstance(entry, dict):
        return f"case {index} is not a dict"
    name, sample, expect = entry.get("name"), entry.get("sample"), entry.get("expect")
    if not isinstance(sample, dict) or not isinstance(expect, bool):
        return f"case {index} needs a dict `sample` and a bool `expect`"
    try:
        plain = json.loads(json.dumps(sample, allow_nan=False))
    except (TypeError, ValueError):
        return f"case {index} sample is not plain JSON"
    label = str(name)[:60] if name else f"case-{index}"
    return ProofCase(name=label, sample=plain, expect=expect, source="generator")


def context_for(code: str, tests: str, spec: CheckSpec, runner: Runner = run_check) -> ProofContext:
    """A `ProofContext` with the generator's cases parsed from `tests`."""
    cases, problems = parse_cases(tests)
    return ProofContext(
        code=code,
        spec=spec,
        generator_cases=tuple(cases),
        case_problems=tuple(problems),
        runner=runner,
    )


def prove(ctx: ProofContext, steps: Sequence[ProofStep] | None = None) -> ProofReport:
    """Run every step and decide whether the extension may go up for approval.

    Args:
        ctx: The code, the spec and the generator's cases.
        steps: The steps to run (`DEFAULT_STEPS` when None).

    Returns:
        The report; `passed` needs every required step passed and no step failed.
    """
    results = tuple(step.run(ctx) for step in (steps if steps is not None else DEFAULT_STEPS))
    status = {r.name: r.status for r in results}
    passed = all(status.get(name) == "passed" for name in REQUIRED_STEPS) and all(
        r.status != "failed" for r in results
    )
    return ProofReport(steps=results, passed=passed)


# --- the cases -------------------------------------------------------------------------------

FRAME_W, FRAME_H = 640, 480
LINE_Y = 200.0
FLAT_LINE: Final = ((0.0, LINE_Y), (640.0, LINE_Y))
SLOPED_LINE: Final = ((0.0, 100.0), (640.0, 300.0))  # y = 200 at the object's centre x = 320
SLOPED_PX: Final = 3.0  # 10 px of error in x moves this line by about 3 px


def make_object(concept: str, top: float) -> Sample:
    """An object centred at x = 320 whose top edge is at `top` and bottom at 420."""
    return {
        "concept": concept,
        "box_xyxy_px": [260.0, top, 380.0, 420.0],
        "mask_top_y_px": top,
        "mask_area_px": 120.0 * (420.0 - top),
        "ground_xy_m": None,
        "score": 0.9,
    }


def make_sample(
    objects: Sequence[Sample], lines: Mapping[str, object], px_per_m: float | None = None
) -> Sample:
    """A full sample dict with every key of `schema.CheckSample`."""
    return {
        "timestamp": "2026-11-03T12:00:00Z",
        "frame_w": FRAME_W,
        "frame_h": FRAME_H,
        "objects": [copy.deepcopy(o) for o in objects],
        "zones": {},
        "lines": dict(lines),
        "px_per_m": px_per_m,
    }


def boundary_cases(spec: CheckSpec) -> tuple[list[ProofCase], list[ProofPair]]:
    """Rulecam's own cases for `spec`, independent of the generator.

    Returns:
        Cases with expectations (or robustness cases with none) and pairs.
    """
    concept = spec.concept
    cases = [
        ProofCase("robust: empty sample", make_sample([], {}), None, "robustness"),
        ProofCase(
            "robust: calibrated, no line",
            make_sample([make_object(concept, 300.0)], {}, px_per_m=100.0),
            None,
            "robustness",
        ),
    ]
    if not spec.lines:
        return cases, []
    line = spec.lines[0]
    flat = {line: [list(p) for p in FLAT_LINE]}
    sloped = {line: [list(p) for p in SLOPED_LINE]}
    over = {
        "just above": make_sample([make_object(concept, LINE_Y - BOUNDARY_PX)], flat),
        "just below": make_sample([make_object(concept, LINE_Y + BOUNDARY_PX)], flat),
        "far above": make_sample([make_object(concept, 60.0)], flat),
        "far below": make_sample([make_object(concept, 340.0)], flat),
    }
    cases.append(ProofCase("boundary: no objects", make_sample([], flat), False, "boundary"))
    cases.append(
        ProofCase(
            "boundary: line missing",
            make_sample([make_object(concept, 60.0)], {}),
            False,
            "boundary",
        )
    )
    if spec.relation is None:
        pairs = [
            ProofPair("across the line differs", over["just above"], over["just below"], False),
            ProofPair("above agrees", over["far above"], over["just above"], True),
            ProofPair("below agrees", over["far below"], over["just below"], True),
        ]
        return cases, pairs
    above = spec.relation == "above"
    for name, sample in over.items():
        cases.append(
            ProofCase(f"boundary: {name}", sample, name.endswith("above") == above, "boundary")
        )
    for side, top in (("above", LINE_Y - SLOPED_PX), ("below", LINE_Y + SLOPED_PX)):
        cases.append(
            ProofCase(
                f"boundary: sloped line, just {side}",
                make_sample([make_object(concept, top)], sloped),
                (side == "above") == above,
                "boundary",
            )
        )
    across = make_object(concept, LINE_Y - BOUNDARY_PX if above else LINE_Y + BOUNDARY_PX)
    not_across = make_object(concept, LINE_Y + BOUNDARY_PX if above else LINE_Y - BOUNDARY_PX)
    for order, objects in (("first", [across, not_across]), ("last", [not_across, across])):
        cases.append(
            ProofCase(
                f"boundary: two objects, the one across {order}",
                make_sample(objects, flat),
                True,
                "boundary",
            )
        )
    return cases, []


# --- running cases ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Batch:
    cases: Sequence[ProofCase]
    pairs: Sequence[ProofPair]

    def samples(self) -> list[Sample]:
        out = [c.sample for c in self.cases]
        for pair in self.pairs:
            out.extend((pair.first, pair.second))
        return out


def run_batch(code: str, batch: _Batch, runner: Runner) -> tuple[list[CaseResult], str | None]:
    """Run all cases and pairs in one sandbox call.

    Returns:
        Per-case and per-pair results, or no results and why the whole run failed.
    """
    try:
        outcomes = runner(code, batch.samples())
    except ExtensionError as exc:
        return [], str(exc)[:200]
    results = [
        _case_result(case, outcome) for case, outcome in zip(batch.cases, outcomes, strict=False)
    ]
    rest = outcomes[len(batch.cases) :]
    for index, pair in enumerate(batch.pairs):
        results.append(_pair_result(pair, rest[2 * index], rest[2 * index + 1]))
    return results, None


def _value(outcome: SampleOutcome) -> bool | None:
    return outcome.result.value if outcome.result is not None else None


def _case_result(case: ProofCase, outcome: SampleOutcome) -> CaseResult:
    got = _value(outcome)
    ok = outcome.error is None and (case.expect is None or got == case.expect)
    return CaseResult(
        name=case.name,
        source=case.source,
        expect=case.expect,
        got=got,
        error=outcome.error,
        passed=ok,
    )


def _pair_result(pair: ProofPair, first: SampleOutcome, second: SampleOutcome) -> CaseResult:
    a, b = _value(first), _value(second)
    error = first.error or second.error
    ok = error is None and (a == b) == pair.same
    return CaseResult(
        name=f"pair: {pair.name}",
        source="boundary",
        expect=None,
        got=None if a is None or b is None else a == b,
        error=error,
        passed=ok,
    )


def all_cases(ctx: ProofContext) -> _Batch:
    """The generator's cases plus Rulecam's boundary cases and pairs."""
    ours, pairs = boundary_cases(ctx.spec)
    return _Batch(cases=(*ctx.generator_cases, *ours), pairs=pairs)


# --- the steps -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatorStep:
    """The sandbox's allow-list."""

    name: str = "validator"

    def run(self, ctx: ProofContext) -> StepResult:
        """See `ProofStep.run`."""
        try:
            validate(ctx.code)
        except ExtensionRejectedError as exc:
            return StepResult(name=self.name, status="failed", detail="; ".join(exc.reasons))
        return StepResult(name=self.name, status="passed", detail="only allowed syntax and names")


@dataclass(frozen=True)
class GeneratorCasesStep:
    """The generator's own test cases, run in the sandbox."""

    name: str = "muse_code_tests"

    def run(self, ctx: ProofContext) -> StepResult:
        """See `ProofStep.run`."""
        cases = ctx.generator_cases
        if len(cases) < MIN_CASES:
            why = "; ".join(ctx.case_problems) or "too few cases"
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"{len(cases)} usable cases, need {MIN_CASES} ({why})",
            )
        results, failure = run_batch(ctx.code, _Batch(cases, ()), ctx.runner)
        return _from_results(self.name, results, failure)


@dataclass(frozen=True)
class BoundaryStep:
    """Rulecam's own boundary, pair and robustness cases."""

    name: str = "boundary"

    def run(self, ctx: ProofContext) -> StepResult:
        """See `ProofStep.run`."""
        ours, pairs = boundary_cases(ctx.spec)
        results, failure = run_batch(ctx.code, _Batch(ours, pairs), ctx.runner)
        return _from_results(self.name, results, failure)


@dataclass(frozen=True)
class MutationStep:
    """Mutants of the check must fail the cases; too many survivors means weak tests."""

    name: str = "mutation"
    threshold: float = KILL_THRESHOLD

    def run(self, ctx: ProofContext) -> StepResult:
        """See `ProofStep.run`."""
        batch = all_cases(ctx)
        original, failure = run_batch(ctx.code, batch, ctx.runner)
        if failure is not None or not all(r.passed for r in original):
            return StepResult(
                name=self.name, status="failed", detail="the check fails its own cases"
            )
        variants = mutants(ctx.code)
        if not variants:
            return StepResult(
                name=self.name, status="failed", detail="no mutants: the code decides nothing"
            )
        survivors = [label for label, code in variants if not _killed(code, batch, ctx.runner)]
        total = len(variants)
        killed = total - len(survivors)
        score = killed / total
        detail = f"{killed} of {total} mutants caught ({score:.0%}; need {self.threshold:.0%})"
        if survivors:
            detail += f"; survived: {', '.join(survivors[:3])}"
        return StepResult(
            name=self.name,
            status="passed" if score >= self.threshold else "failed",
            detail=detail,
            metrics={"killed": float(killed), "total": float(total), "score": round(score, 3)},
        )


@dataclass(frozen=True)
class ImageScenesStep:
    """Muse Image scenes need live models; this build does not render them."""

    name: str = "muse_image_scenes"

    def run(self, ctx: ProofContext) -> StepResult:
        """See `ProofStep.run`."""
        del ctx
        return StepResult(
            name=self.name,
            status="skipped",
            detail="not run: rendered scenes need live Muse Image and SAM 3.1",
        )


DEFAULT_STEPS: Final[tuple[ProofStep, ...]] = (
    ValidatorStep(),
    GeneratorCasesStep(),
    BoundaryStep(),
    MutationStep(),
    ImageScenesStep(),
)


def _from_results(name: str, results: list[CaseResult], failure: str | None) -> StepResult:
    if failure is not None:
        return StepResult(name=name, status="failed", detail=f"the run failed: {failure}")
    failed = [r.name for r in results if not r.passed]
    detail = f"{len(results) - len(failed)} of {len(results)} cases pass"
    if failed:
        detail += f"; failing: {', '.join(failed[:3])}"
    return StepResult(
        name=name,
        status="failed" if failed else "passed",
        detail=detail,
        metrics={"passed": float(len(results) - len(failed)), "total": float(len(results))},
        cases=tuple(results),
    )


def _killed(code: str, batch: _Batch, runner: Runner) -> bool:
    results, failure = run_batch(code, batch, runner)
    return failure is not None or not all(r.passed for r in results)


# --- mutants ---------------------------------------------------------------------------------

_FLIPS: Final[dict[type[ast.cmpop], type[ast.cmpop]]] = {
    ast.Lt: ast.Gt,
    ast.Gt: ast.Lt,
    ast.LtE: ast.GtE,
    ast.GtE: ast.LtE,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}


@dataclass(frozen=True)
class _Site:
    kind: str
    path: int
    index: int = 0


def _numeric(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and node.value != 0
    )


def _rounding_digits(tree: ast.Module) -> set[int]:
    """Ids of the `ndigits` constants of `round(...)` calls: presentation, not decisions."""
    return {
        id(node.args[1])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "round"
        and len(node.args) == 2  # noqa: PLR2004 - round(number, ndigits)
    }


def _sites(tree: ast.Module) -> list[_Site]:
    sites: list[_Site] = []
    shown_only = _rounding_digits(tree)
    for path, node in enumerate(ast.walk(tree)):
        if isinstance(node, ast.Compare):
            sites.extend(
                _Site("compare", path, i) for i, op in enumerate(node.ops) if type(op) in _FLIPS
            )
        elif isinstance(node, ast.BoolOp):
            sites.append(_Site("boolop", path))
            sites.extend(_Site("drop", path, i) for i in range(len(node.values)))
        elif _numeric(node) and id(node) not in shown_only:
            sites.extend((_Site("up", path), _Site("down", path)))
        elif isinstance(node, ast.If):
            sites.append(_Site("if_true", path))
        elif isinstance(node, ast.Dict) and _value_key(node) is not None:
            sites.append(_Site("negate", path))
    return sites


def _value_key(node: ast.Dict) -> int | None:
    for i, key in enumerate(node.keys):
        if isinstance(key, ast.Constant) and key.value == "value":
            return i
    return None


def _apply(node: ast.AST, site: _Site) -> str:
    """Mutate `node` in place for `site`; return a short label."""
    if isinstance(node, ast.Compare) and site.kind == "compare":
        old = node.ops[site.index]
        node.ops[site.index] = _FLIPS[type(old)]()
        return f"line {node.lineno}: {type(old).__name__}→{type(node.ops[site.index]).__name__}"
    if isinstance(node, ast.BoolOp) and site.kind == "boolop":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return f"line {node.lineno}: and↔or"
    if isinstance(node, ast.BoolOp) and site.kind == "drop":
        del node.values[site.index]
        return f"line {node.lineno}: drop operand {site.index + 1}"
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and site.kind in {"up", "down"}
    ):
        node.value = _nudge(node.value, up=site.kind == "up")
        return f"line {node.lineno}: number {site.kind} 10%"
    if isinstance(node, ast.If) and site.kind == "if_true":
        node.test = ast.Constant(value=True)
        return f"line {node.lineno}: if-test dropped"
    if isinstance(node, ast.Dict) and site.kind == "negate":
        key = _value_key(node)
        if key is not None:
            node.values[key] = ast.UnaryOp(op=ast.Not(), operand=node.values[key])
        return f"line {node.lineno}: value negated"
    msg = f"unknown mutation site {site.kind}"
    raise ValueError(msg)


def _nudge(value: int | float, *, up: bool) -> int | float:
    if isinstance(value, int):
        step = max(1, round(abs(value) * 0.1))
        return value + step if up else value - step
    return value * (1.1 if up else 0.9)


def mutants(code: str, limit: int = MAX_MUTANTS) -> list[tuple[str, str]]:
    """Code-level mutants of `code` as `(label, source)`, at most `limit`, in source order.

    When there are more sites than `limit`, evenly spaced ones are kept.
    """
    sites = _sites(ast.parse(code))
    if len(sites) > limit:
        step = len(sites) / limit
        sites = [sites[int(i * step)] for i in range(limit)]
    out: list[tuple[str, str]] = []
    for site in sites:
        tree = ast.parse(code)
        node = next(n for i, n in enumerate(ast.walk(tree)) if i == site.path)
        label = _apply(node, site)
        mutated = ast.unparse(ast.fix_missing_locations(tree))
        if mutated != code:
            out.append((label, mutated))
    return out
