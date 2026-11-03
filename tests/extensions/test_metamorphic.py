from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from rulecam.extensions.metamorphic import (
    Answer,
    MetamorphicReport,
    MonotoneRule,
    Relation,
    RelationResult,
    agrees,
    check_relations,
    default_relations,
    moved_concept,
    reversed_objects,
    run_answers,
    scaled,
    translated,
    translation,
    violations_of,
    with_unrelated,
)
from rulecam.extensions.scenegen import (
    CheckSpec,
    SampleJSON,
    copy_sample,
    generate,
    make_object,
    make_sample,
)
from tests.extensions.checks import (
    BUGGY_ANY_CONCEPT,
    BUGGY_FIRST_OBJECT,
    BUGGY_FIXED_Y,
    BUGGY_PIXEL_MARGIN,
    BUGGY_SLIGHTLY_ABOVE,
    SPEC,
    TOP_ABOVE,
)
from tests.extensions.inprocess import FakeOutcome, InProcessRunner

UP = MonotoneRule(concept="pallet", direction="up")


def _report(code: str, *, n_random: int = 40) -> MetamorphicReport:
    samples = generate(SPEC, n_random=n_random, seed=7)
    return check_relations(code, samples, default_relations(SPEC, [UP]), InProcessRunner())


def _relation(report: MetamorphicReport, name: str) -> RelationResult:
    return next(r for r in report.relations if r.name == name)


def _one_pallet(
    box: tuple[float, float, float, float] = (500.0, 280.0, 580.0, 340.0),
) -> SampleJSON:
    return make_sample(SPEC, [make_object("pallet", box, px_per_m=100.0)])


# --- answers --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "expect", "holds"),
    [
        (Answer(value=True), Answer(value=True), "same", True),
        (Answer(value=True), Answer(value=False), "same", False),
        (Answer(value=None), Answer(value=None), "same", True),
        (Answer(error="x"), Answer(error="y"), "same", True),
        (Answer(error="x"), Answer(value=False), "same", False),
        (Answer(value=False), Answer(error="y"), "same", False),
        (Answer(value=True), Answer(value=False), "keeps_true", False),
        (Answer(value=True), Answer(error="y"), "keeps_true", False),
        (Answer(value=False), Answer(value=True), "keeps_true", True),
        (Answer(value=None), Answer(value=False), "keeps_true", True),
        (Answer(error="x"), Answer(value=False), "keeps_true", True),
    ],
)
def test_agrees(before: Answer, after: Answer, expect: str, holds: bool) -> None:
    assert agrees(before, after, expect) is holds  # type: ignore[arg-type]


def test_run_answers_batches_and_maps_outcomes() -> None:
    runner = InProcessRunner()
    samples = generate(SPEC, n_random=0)[:20]
    answers = run_answers(runner, BUGGY_FIRST_OBJECT, samples, batch_size=7)
    assert runner.batches == [7, 7, 6]
    assert answers[0].error is not None  # the empty scene crashes this check
    assert answers[0].value is None
    assert all(a.error is None for a in answers[1:])


def test_run_answers_rejects_a_runner_that_drops_samples() -> None:
    def short(code: str, samples: Sequence[Mapping[str, object]], /) -> list[FakeOutcome]:
        return []

    with pytest.raises(ValueError, match="0 outcomes for 1 samples"):
        run_answers(short, TOP_ABOVE, [_one_pallet()])


def test_run_answers_with_no_samples_never_calls_the_runner() -> None:
    runner = InProcessRunner()
    assert run_answers(runner, TOP_ABOVE, []) == []
    assert runner.batches == []


# --- transforms -----------------------------------------------------------------------------


def test_transforms_are_pure() -> None:
    sample = make_sample(
        SPEC,
        [
            make_object("pallet", (500.0, 280.0, 580.0, 340.0), px_per_m=100.0),
            make_object("pallet", (700.0, 100.0, 780.0, 160.0), px_per_m=100.0),
        ],
    )
    before = copy_sample(sample)
    for result in (
        translated(sample, 10.0, 5.0),
        with_unrelated(sample, "traffic cone"),
        reversed_objects(sample),
        scaled(sample, 2.0),
        moved_concept(sample, "pallet", 0.0, -10.0),
    ):
        assert result is not None
        assert result != sample
    assert sample == before


def test_translated_moves_everything_and_keeps_the_scene_in_frame() -> None:
    sample = _one_pallet()
    moved = translated(sample, 10.0, -5.0)
    assert moved is not None
    assert moved["objects"][0]["box_xyxy_px"] == [510.0, 275.0, 590.0, 335.0]
    assert moved["objects"][0]["mask_top_y_px"] == 275.0
    assert moved["lines"]["yellow"] == [[110.0, 295.0], [1190.0, 295.0]]
    assert moved["zones"]["dock"][0] == [210.0, 395.0]
    ground = moved["objects"][0]["ground_xy_m"]
    assert ground == pytest.approx([5.5, 3.35])
    assert translated(sample, 2000.0, 0.0) is None


def test_translation_tries_the_other_signs_when_the_first_leaves_the_frame() -> None:
    sample = make_sample(
        SPEC, [make_object("pallet", (1190.0, 600.0, 1270.0, 700.0), px_per_m=100.0)]
    )
    moved = translation().transform(sample)
    assert moved is not None
    assert moved["objects"][0]["box_xyxy_px"][0] == 1167.0


def test_scaled_multiplies_pixels_and_keeps_ground() -> None:
    sample = _one_pallet()
    big = scaled(sample, 2.0)
    assert big is not None
    assert big["frame_w"] == 2560
    assert big["px_per_m"] == 200.0
    assert big["objects"][0]["box_xyxy_px"] == [1000.0, 560.0, 1160.0, 680.0]
    assert big["objects"][0]["mask_area_px"] == pytest.approx(
        4 * sample["objects"][0]["mask_area_px"]
    )
    assert big["objects"][0]["ground_xy_m"] == sample["objects"][0]["ground_xy_m"]
    sample["px_per_m"] = None
    assert scaled(sample, 2.0) is None


def test_transforms_that_do_not_apply_return_none() -> None:
    one = _one_pallet()
    assert reversed_objects(one) is None
    assert moved_concept(one, "forklift", 0.0, -10.0) is None
    assert moved_concept(one, "pallet", 0.0, -1000.0) is None
    tiny = CheckSpec(concepts=("pallet",), frame_w=30, frame_h=30)
    assert with_unrelated(make_sample(tiny, []), "traffic cone") is None


def test_default_relations_include_declared_monotone_directions() -> None:
    names = [r.name for r in default_relations(SPEC, [UP])]
    assert names == ["translate", "unrelated_object", "reorder", "scale", "monotone:pallet:up"]
    no_unrelated = CheckSpec(concepts=("pallet",), unrelated_concepts=())
    assert "unrelated_object" not in [r.name for r in default_relations(no_unrelated)]


# --- check_relations ------------------------------------------------------------------------


def test_a_correct_check_passes_every_relation() -> None:
    report = _report(TOP_ABOVE)
    assert report.passed
    assert report.errors == 0
    for result in report.relations:
        assert result.passed
        assert result.checked > 0
        assert result.checked + result.skipped == report.samples
        assert result.counterexample is None


def test_a_hard_coded_row_breaks_translation_with_a_shrunk_counterexample() -> None:
    report = _report(BUGGY_FIXED_Y)
    result = _relation(report, "translate")
    assert not report.passed
    assert not result.passed
    assert result.violations > 0
    example = result.counterexample
    assert example is not None
    assert len(example.sample["objects"]) == 1
    assert example.before.value != example.after.value
    assert not _relation(report, "scale").passed  # pixels doubled, the row did not


def test_counting_every_concept_breaks_the_unrelated_object_relation() -> None:
    report = _report(BUGGY_ANY_CONCEPT)
    result = _relation(report, "unrelated_object")
    assert not result.passed
    example = result.counterexample
    assert example is not None
    assert example.before.value is False
    assert example.after.value is True
    assert [o["concept"] for o in example.transformed["objects"]][-1] == "traffic cone"


def test_reading_only_the_first_object_breaks_reordering_and_errors() -> None:
    report = _report(BUGGY_FIRST_OBJECT)
    assert not _relation(report, "reorder").passed
    assert report.errors > 0
    assert not report.passed


def test_a_fixed_pixel_margin_breaks_scaling() -> None:
    report = _report(BUGGY_PIXEL_MARGIN)
    assert not _relation(report, "scale").passed
    assert _relation(report, "translate").passed


def test_an_upper_bound_breaks_the_declared_monotone_direction() -> None:
    # Boundary tops sit 1-8 px above the line, so a 15 px step crosses the check's 20 px bound.
    samples = generate(SPEC, n_random=40, seed=7)
    step = MonotoneRule(concept="pallet", direction="up", step_px=15.0)
    report = check_relations(
        BUGGY_SLIGHTLY_ABOVE, samples, default_relations(SPEC, [step]), InProcessRunner()
    )
    result = _relation(report, "monotone:pallet:up")
    assert not result.passed
    example = result.counterexample
    assert example is not None
    assert example.before.value is True
    assert example.after.value is False


def test_without_shrinking_the_first_raw_violation_is_reported() -> None:
    samples = generate(SPEC, n_random=0)
    report = check_relations(
        BUGGY_FIXED_Y, samples, [translation()], InProcessRunner(), shrink_failures=False
    )
    example = report.relations[0].counterexample
    assert example is not None
    assert example.sample in samples


def test_violations_of_marks_inapplicable_candidates_false() -> None:
    relation = Relation(
        name="never", description="never applies", expect="same", transform=lambda s: None
    )
    judge = violations_of(BUGGY_FIXED_Y, relation, InProcessRunner())
    assert judge([_one_pallet(), _one_pallet()]) == [False, False]


def test_the_report_round_trips_as_json() -> None:
    report = _report(BUGGY_FIXED_Y, n_random=10)
    assert MetamorphicReport.model_validate_json(report.model_dump_json()) == report
