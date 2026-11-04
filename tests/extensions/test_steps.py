"""The add-on proof steps: common-sense relations and the Z3 math proof, on real checks."""

from __future__ import annotations

from rulecam.extensions.generate import CheckSpec
from rulecam.extensions.proof import DEFAULT_STEPS, ProofContext, context_for, prove
from rulecam.extensions.steps import CommonSenseStep, MathProofStep, proof_steps
from tests.extensions.checks import BUGGY_ANY_CONCEPT, BUGGY_FIXED_Y, YELLOW
from tests.fakes.extensions import EXAMPLE_CHECK, EXAMPLE_TESTS

ABOVE = CheckSpec(
    request="alert if a pallet is above the yellow line",
    concept="pallet",
    lines=("yellow",),
    relation="above",
)
LINES = {"yellow": YELLOW}


def test_a_correct_check_passes_both_steps() -> None:
    ctx = ProofContext(code=EXAMPLE_CHECK, spec=ABOVE)
    sense = CommonSenseStep(lines=LINES, n_random=40).run(ctx)
    math = MathProofStep().run(ctx)
    assert sense.status == "passed", sense.detail
    assert sense.metrics["held"] == sense.metrics["relations"] == 5.0
    assert math.status == "passed", math.detail
    assert math.metrics == {"proved": 4.0, "counterexamples": 0.0, "undecided": 0.0}


def test_a_fixed_pixel_check_breaks_translation_in_both() -> None:
    ctx = ProofContext(code=BUGGY_FIXED_Y, spec=ABOVE)
    sense = CommonSenseStep(lines=LINES, n_random=40).run(ctx)
    math = MathProofStep().run(ctx)
    assert sense.status == "failed"
    assert "translate" in sense.detail
    assert math.status == "failed"
    assert "translation_invariant: counterexample" in math.detail


def test_counting_other_concepts_is_refuted_by_z3() -> None:
    math = MathProofStep().run(ProofContext(code=BUGGY_ANY_CONCEPT, spec=ABOVE))
    assert math.status == "failed"
    assert "no pallet" in math.detail


def test_below_requests_move_objects_down_and_frames_fit_the_lines() -> None:
    below = ABOVE.model_copy(update={"relation": "below"})
    far = {"yellow": ((100.0, 900.0), (1800.0, 900.0))}
    step = CommonSenseStep(lines=far, n_random=10)
    result = step.run(ProofContext(code=EXAMPLE_CHECK, spec=below))
    assert result.metrics["relations"] == 5.0
    assert "monotone" in result.detail or result.status == "passed"


def test_no_relation_and_no_lines_still_runs() -> None:
    spec = CheckSpec(request="alert if a pallet is in view", concept="pallet")
    ctx = ProofContext(code=EXAMPLE_CHECK, spec=spec)
    sense = CommonSenseStep(n_random=10).run(ctx)
    assert sense.metrics["relations"] == 4.0
    assert MathProofStep().run(ctx).metrics["proved"] >= 2.0


def test_undecided_properties_are_skipped_never_passed() -> None:
    code = "import math\n" + EXAMPLE_CHECK.replace("round(highest, 1)", "math.atan2(highest, 1.0)")
    math = MathProofStep().run(ProofContext(code=code, spec=ABOVE))
    assert math.status == "skipped"
    assert math.metrics["undecided"] == 3.0
    assert "total: unsupported" in math.detail


def test_proof_steps_append_the_add_ons_and_the_proof_passes() -> None:
    steps = proof_steps(LINES, (1280, 720))
    assert [s.name for s in steps][: len(DEFAULT_STEPS)] == [s.name for s in DEFAULT_STEPS]
    assert [s.name for s in steps][-2:] == ["common_sense", "math_proof"]
    report = prove(context_for(EXAMPLE_CHECK, EXAMPLE_TESTS, ABOVE), steps)
    assert report.passed, [(s.name, s.status, s.detail) for s in report.steps]
