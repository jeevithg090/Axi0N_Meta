"""The proof: generator cases, our boundary cases and mutants decide what may be approved."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rulecam.extensions.generate import CheckSpec
from rulecam.extensions.proof import (
    DEFAULT_STEPS,
    MutationStep,
    ProofContext,
    StepResult,
    boundary_cases,
    context_for,
    mutants,
    parse_cases,
    prove,
)
from tests.fakes.extensions import EXAMPLE_CHECK, EXAMPLE_TESTS

ABOVE = CheckSpec(
    request="Alert if a pallet is stacked above the yellow line",
    concept="pallet",
    lines=("yellow",),
    relation="above",
)

# Looks fine, but compares against the line's first point only: sloped lines break it.
IGNORES_SLOPE = EXAMPLE_CHECK.replace(
    "above_px = line_y_at(line, (box[0] + box[2]) / 2) - top", "above_px = line[0][1] - top"
)
# Always says no: the generator's "above" case fails.
ALWAYS_NO = 'def check(sample):\n    return {"value": False, "why": "never"}\n'


def _status(report_steps: tuple[StepResult, ...], name: str) -> str:
    return next(s.status for s in report_steps if s.name == name)


def test_the_reference_check_proves() -> None:
    report = prove(context_for(EXAMPLE_CHECK, EXAMPLE_TESTS, ABOVE))
    assert report.passed, [s.detail for s in report.steps]
    mutation = next(s for s in report.steps if s.name == "mutation")
    assert mutation.metrics["score"] >= 0.8
    assert _status(report.steps, "muse_image_scenes") == "skipped"


def test_a_slope_blind_check_fails_our_boundary() -> None:
    report = prove(context_for(IGNORES_SLOPE, EXAMPLE_TESTS, ABOVE))
    assert not report.passed
    boundary = next(s for s in report.steps if s.name == "boundary")
    assert boundary.status == "failed"
    assert "sloped line" in boundary.detail


def test_a_check_that_always_says_no_fails() -> None:
    report = prove(context_for(ALWAYS_NO, EXAMPLE_TESTS, ABOVE))
    assert _status(report.steps, "muse_code_tests") == "failed"
    assert _status(report.steps, "mutation") == "failed"
    assert not report.passed


def test_invalid_code_fails_the_validator_and_every_run() -> None:
    report = prove(
        context_for("import os\ndef check(sample):\n    return {}", EXAMPLE_TESTS, ABOVE)
    )
    assert _status(report.steps, "validator") == "failed"
    assert not report.passed


def test_too_few_generator_cases_fail() -> None:
    ctx = context_for(EXAMPLE_CHECK, "CASES = []", ABOVE)
    report = prove(ctx)
    step = next(s for s in report.steps if s.name == "muse_code_tests")
    assert step.status == "failed"
    assert "need 3" in step.detail


def test_weak_tests_let_mutants_survive() -> None:
    # Only "no pallet" style cases: nothing pins down the comparison.
    weak = EXAMPLE_TESTS.split('    {\n        "name": "no-pallets"')[0] + "]\n"
    ctx = context_for(EXAMPLE_CHECK, weak, CheckSpec(request="pallets above", concept="pallet"))
    result = MutationStep().run(ctx)
    assert result.status == "failed"
    assert "survived" in result.detail


def test_tests_are_read_as_data_never_run() -> None:
    cases, problems = parse_cases("import os\nos.system('rm -rf /')\nCASES = [1, {'x': 1}]")
    assert cases == []
    assert len(problems) == 2
    assert parse_cases("CASES = __import__('os')")[1] == ["test file is not a literal: ValueError"]
    assert parse_cases("x = 1")[1] == ["no `CASES = [...]` in the test file"]
    assert parse_cases("CASES = {}")[1] == ["CASES is not a list"]


def test_parse_keeps_valid_cases() -> None:
    cases, problems = parse_cases(EXAMPLE_TESTS)
    assert problems == []
    assert [c.name for c in cases][:2] == ["top-10-px-above", "top-10-px-below"]
    assert all(c.source == "generator" for c in cases)


@pytest.mark.parametrize("relation", ["above", "below", None])
def test_boundary_cases_follow_the_relation(relation: str | None) -> None:
    spec = CheckSpec(request="pallet line", concept="pallet", lines=("yellow",), relation=relation)
    cases, pairs = boundary_cases(spec)
    expected = {c.name: c.expect for c in cases}
    if relation is None:
        assert len(pairs) == 3
        assert "boundary: just above" not in expected
    else:
        assert expected["boundary: just above"] is (relation == "above")
        assert expected["boundary: two objects, the one across last"] is True
    assert expected["boundary: line missing"] is False


def test_no_line_means_only_robustness() -> None:
    cases, pairs = boundary_cases(CheckSpec(request="count people", concept="person"))
    assert {c.source for c in cases} == {"robustness"}
    assert pairs == []


def test_mutants_cover_the_operators() -> None:
    labels = [label for label, _ in mutants(EXAMPLE_CHECK)]
    text = " ".join(labels)
    for kind in ("Gt→Lt", "number up", "if-test dropped", "value negated"):
        assert kind in text
    assert len(labels) <= 60


@dataclass(frozen=True)
class AlwaysFails:
    """An add-on step: shows that registered steps count."""

    name: str = "addon"

    def run(self, ctx: ProofContext) -> StepResult:
        del ctx
        return StepResult(name=self.name, status="failed", detail="add-on says no")


def test_registered_steps_count() -> None:
    ctx = context_for(EXAMPLE_CHECK, EXAMPLE_TESTS, ABOVE)
    report = prove(ctx, (*DEFAULT_STEPS, AlwaysFails()))
    assert not report.passed
    assert report.steps[-1].name == "addon"
