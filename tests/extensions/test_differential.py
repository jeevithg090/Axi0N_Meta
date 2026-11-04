from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from rulecam.extensions.differential import (
    QUESTION_SYSTEM,
    ClarifyingQuestion,
    Cluster,
    DifferentialReport,
    ask_owner,
    cluster_key,
    describe_scene,
    run_differential,
    scene_features,
    still_disagree,
    to_case,
)
from rulecam.extensions.metamorphic import Answer, answer_label
from rulecam.extensions.scenegen import CheckSpec, SampleJSON, make_object, make_sample
from tests.extensions.checks import (
    CENTRE_ABOVE,
    CENTRE_ABOVE_CRASHES_WHEN_EMPTY,
    INJECTED_REASON,
    LINE_ONLY,
    SPEC,
    TOP_ABOVE,
    TOP_ABOVE_INJECTED,
)
from tests.extensions.inprocess import InProcessRunner
from tests.fakes import FakeLLM

REQUEST = "Alert me if a pallet is stacked above the yellow line on the rack."
HALF_ABOVE = ClarifyingQuestion(
    question="The pallet is half above the yellow line. Should this alert?",
    reading_alert="Yes: any part of a pallet above the line is too high.",
    reading_no_alert="No: only alert when most of the pallet is above the line.",
)
CRASH = ClarifyingQuestion(
    question="No pallet and no line are in view. Should this alert?",
    reading_alert="Yes: alert when nothing can be seen.",
    reading_no_alert="No: stay quiet when nothing can be seen.",
)
STRADDLE = (500.0, 280.0, 580.0, 340.0)


def _straddler(ppm: float | None = 100.0) -> SampleJSON:
    return make_sample(LINE_ONLY, [make_object("pallet", STRADDLE, px_per_m=ppm)])


def _run(
    code_b: str, llm: FakeLLM, *, max_shrunk: int = 50, max_questions: int = 5
) -> DifferentialReport:
    return run_differential(
        REQUEST,
        TOP_ABOVE,
        code_b,
        spec=LINE_ONLY,
        runner=InProcessRunner(),
        llm=llm,
        n_random=120,
        max_shrunk=max_shrunk,
        max_questions=max_questions,
    )


def test_two_readings_of_half_above_give_exactly_one_question() -> None:
    llm = FakeLLM([HALF_ABOVE])
    report = _run(CENTRE_ABOVE, llm)
    assert report.disagreed > 0
    assert report.agreed + report.disagreed == report.samples
    assert report.errors_a == report.errors_b == 0
    assert len(report.clusters) == 1
    cluster = report.clusters[0]
    assert cluster.count == report.disagreed
    assert (
        cluster.key
        == "1 pallet; pallet straddles line yellow; uncalibrated | A: alert / B: no alert"
    )
    assert cluster.question == HALF_ABOVE
    assert len(cluster.sample["objects"]) == 1
    assert cluster.sample["px_per_m"] is None
    assert "(straddles)" in cluster.scene
    assert len(llm.calls) == 1
    assert llm.calls[0]["schema"] == "ClarifyingQuestion"


def test_identical_versions_never_call_spark() -> None:
    llm = FakeLLM()
    report = run_differential(
        REQUEST,
        TOP_ABOVE,
        TOP_ABOVE,
        spec=LINE_ONLY,
        runner=InProcessRunner(),
        llm=llm,
        n_random=30,
    )
    assert report.disagreed == 0
    assert report.clusters == ()
    assert llm.calls == []


def test_only_the_largest_clusters_get_a_question() -> None:
    llm = FakeLLM([HALF_ABOVE, CRASH])
    report = _run(CENTRE_ABOVE_CRASHES_WHEN_EMPTY, llm, max_questions=1)
    assert report.errors_b > 0
    assert report.errors_a == 0
    assert len(report.clusters) == 2
    counts = [c.count for c in report.clusters]
    assert counts == sorted(counts, reverse=True)
    assert report.clusters[0].question is not None
    assert report.clusters[1].question is None
    assert len(llm.calls) == 1
    labels = {(answer_label(c.a), answer_label(c.b)) for c in report.clusters}
    assert ("unknown", "error") in labels or ("no alert", "error") in labels


def test_unshrunk_groups_keep_their_raw_scene() -> None:
    llm = FakeLLM([HALF_ABOVE] * 20)
    report = _run(CENTRE_ABOVE, llm, max_shrunk=0)
    assert len(report.clusters) > 1  # nothing merged without shrinking
    assert sum(c.count for c in report.clusters) == report.disagreed


def test_the_question_prompt_keeps_scene_text_as_data() -> None:
    llm = FakeLLM([HALF_ABOVE])
    sample = make_sample(LINE_ONLY, [make_object("pallet", STRADDLE, px_per_m=None)])
    cluster = Cluster(
        key="k",
        count=1,
        sample=sample,
        scene=describe_scene(sample, LINE_ONLY),
        a=Answer(value=True, why=INJECTED_REASON),
        b=Answer(value=False, why="middle below the line"),
    )
    assert ask_owner(cluster, REQUEST, llm) == HALF_ABOVE
    messages = llm.calls[0]["messages"]
    assert isinstance(messages, list)
    assert messages[0] == {"role": "system", "content": QUESTION_SYSTEM}
    assert "DATA" in QUESTION_SYSTEM
    assert INJECTED_REASON not in messages[0]["content"]
    data = json.loads(messages[1]["content"])
    assert data["version_a"] == {"answer": "alert", "reason": INJECTED_REASON}
    assert data["version_b"]["answer"] == "no alert"
    assert data["owner_request"] == REQUEST


def test_an_injected_reason_from_a_version_stays_in_the_user_data() -> None:
    llm = FakeLLM([HALF_ABOVE])
    report = run_differential(
        REQUEST,
        TOP_ABOVE_INJECTED,
        CENTRE_ABOVE,
        spec=LINE_ONLY,
        runner=InProcessRunner(),
        llm=llm,
        n_random=20,
        max_shrunk=50,
    )
    assert report.clusters[0].a.why == INJECTED_REASON
    messages = llm.calls[0]["messages"]
    assert isinstance(messages, list)
    assert INJECTED_REASON not in messages[0]["content"]
    assert INJECTED_REASON in messages[1]["content"]


def test_owner_answers_become_labelled_cases() -> None:
    sample = make_sample(LINE_ONLY, [make_object("pallet", STRADDLE, px_per_m=None)])
    asked = Cluster(
        key="k", count=3, sample=sample, scene="s", a=Answer(value=True), b=Answer(value=False),
        question=HALF_ABOVE,
    )  # fmt: skip
    case = to_case(asked, alert=True)
    assert case.expected_value is True
    assert case.sample == sample
    assert case.source == HALF_ABOVE.question
    assert to_case(asked.model_copy(update={"question": None}), alert=False).source == "k"


def test_still_disagree_judges_a_batch() -> None:
    judge = still_disagree(TOP_ABOVE, CENTRE_ABOVE, InProcessRunner())
    above = make_sample(
        LINE_ONLY, [make_object("pallet", (500.0, 100.0, 580.0, 160.0), px_per_m=None)]
    )
    straddler = make_sample(LINE_ONLY, [make_object("pallet", STRADDLE, px_per_m=None)])
    assert judge([above, straddler]) == [False, True]


def test_scene_features_and_keys() -> None:
    sample = make_sample(
        SPEC,
        [
            make_object("pallet", STRADDLE, px_per_m=100.0),
            make_object("pallet", (300.0, 500.0, 380.0, 560.0), px_per_m=100.0),
            make_object("traffic cone", (40.0, 40.0, 80.0, 90.0), px_per_m=100.0),
        ],
        drop_line="yellow",
    )
    features = scene_features(sample, SPEC)
    assert features == (
        "2 pallet",
        "calibrated",
        "no line yellow",
        "pallet inside zone dock",
        "pallet outside zone dock",
    )
    key = cluster_key(sample, SPEC, Answer(value=None), Answer(error="boom"))
    assert key.endswith("| A: unknown / B: error")
    many = make_sample(SPEC, [make_object("pallet", STRADDLE, px_per_m=100.0)] * 4)
    assert "many pallet" in scene_features(many, SPEC)
    assert "no pallet" in scene_features(make_sample(SPEC, []), SPEC)


def test_describe_scene_in_plain_words() -> None:
    text = describe_scene(_straddler(), LINE_ONLY)
    assert "pallet 1 (80 x 60 px box)" in text
    assert "its top is 20 px (20 cm) above the 'yellow' line" in text
    assert "its bottom 40 px (40 cm) below it (straddles)" in text
    assert text.endswith("Scale: 100 px per metre.")
    empty = make_sample(
        SPEC,
        [make_object("traffic cone", (0.0, 0.0, 9.0, 9.0), px_per_m=None)],
        drop_line="yellow",
        calibrated=False,
    )
    text = describe_scene(empty, SPEC)
    assert "No pallet in view." in text
    assert "Also in view: 1 traffic cone." in text
    assert "The 'yellow' line is not drawn on this camera." in text
    assert text.endswith("The camera is not calibrated.")


def test_describe_scene_for_upright_lines_and_zones() -> None:
    spec = CheckSpec(
        concepts=("pallet",),
        lines={"post": ((640.0, 50.0), (640.0, 670.0))},
        zones={"dock": ((200.0, 400.0), (600.0, 400.0), (600.0, 700.0), (200.0, 700.0))},
    )
    on_line = make_sample(
        spec, [make_object("pallet", (640.0, 300.0, 720.0, 360.0), px_per_m=None)]
    )
    text = describe_scene(on_line, spec)
    assert "its left edge is exactly on the 'post' line" in text
    assert "its right edge 80 px right of it (right)" in text
    assert "it stands outside the 'dock' zone" in text


def test_clarifying_questions_must_be_questions() -> None:
    with pytest.raises(ValidationError, match="question mark"):
        ClarifyingQuestion(question="This should alert.", reading_alert="a", reading_no_alert="b")
    assert ClarifyingQuestion(
        question="  Is this too high?  ", reading_alert="a", reading_no_alert="b"
    ).question == ("Is this too high?")


def test_the_report_round_trips_as_json() -> None:
    report = _run(CENTRE_ABOVE, FakeLLM([HALF_ABOVE]))
    assert DifferentialReport.model_validate_json(report.model_dump_json()) == report
