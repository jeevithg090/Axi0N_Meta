"""The owner's answers to the planner's questions, and the re-run they trigger (WS4)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from rulecam.contracts import RequestItem, SitePlan
from rulecam.contracts.core import Zone
from rulecam.planner.acceptance import calibration_question
from rulecam.planner.answers import (
    Answer,
    AnswerError,
    AnswerRecord,
    PlanBusyError,
    PlanQuestion,
    TapPoint,
    apply_answers,
    plan_zones,
    record_answers,
    saved_answers,
    saved_questions,
)
from rulecam.planner.errors import PipelineError
from rulecam.planner.pipeline import AskResult, PipelineInput, PlanStore, ProbeResult
from rulecam.planner.runner import PlannerService
from rulecam.planner.site_config import ClickCalibration, SiteConfig
from rulecam.planner.steps.ask import Question
from tests.fakes import EPOCH, FakeClock
from tests.planner.test_pipeline import HANDWASH, _pipeline, _spark

SQUARE_PX = ((100.0, 300.0), (300.0, 300.0), (320.0, 420.0), (80.0, 420.0))
SQUARE_M = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
DOOR = Zone(
    zone_id="back_door",
    camera_id="cam1",
    kind="polygon",
    polygon_px=((0.0, 0.0), (50.0, 0.0), (50.0, 100.0), (0.0, 100.0)),
)
SCHEDULE = PlanQuestion(
    step="ask", question_id="q2", text="Opening hours?", target_field="schedule"
)
EXIT = PlanQuestion(
    step="ask",
    question_id="q3",
    text="Which door is the exit?",
    target_field="exit",
    requires_frame_tap=True,
    camera_id="cam1",
)
CALIBRATE = PlanQuestion(
    step="accept", **calibration_question(1, "cam1", ["keep 2 m"]).model_dump()
)


def inputs() -> PipelineInput:
    return PipelineInput(
        plan_id="p1", site_name="s", request=(RequestItem(kind="text", text=HANDWASH),)
    )


def test_words_taps_and_calibrations_become_the_next_runs_inputs() -> None:
    answers = [
        Answer(question_id="q2", text="  6am to 10pm  "),
        Answer(question_id="q3", tap=TapPoint(x=20, y=40)),
        Answer(
            question_id="calibrate-1",
            calibration=ClickCalibration(image_px=SQUARE_PX, ground_m=SQUARE_M),
        ),
    ]
    updated = apply_answers(inputs(), [SCHEDULE, EXIT, CALIBRATE], answers, [DOOR])
    texts = [item.text for item in updated.request]
    assert texts[0] == HANDWASH
    assert texts[1] == 'Answer to the planner\'s question "Opening hours?": 6am to 10pm'
    assert texts[2] == (
        'Answer to the planner\'s question "Which door is the exit?": '
        "the point (20, 40) on camera cam1, inside zone back_door"
    )
    assert len(texts) == 3  # a calibration alone adds no words
    calibration = updated.calibrations["cam1"]
    assert calibration.method == "clicks4"
    assert calibration.err_m < 0.01
    assert updated.plan_id == "p1"


def test_a_tap_outside_every_zone_is_described_by_its_point() -> None:
    updated = apply_answers(
        inputs(), [EXIT], [Answer(question_id="q3", text="the red one", tap=TapPoint(x=400, y=5))]
    )
    assert updated.request[-1].text is not None
    assert updated.request[-1].text.endswith("the red one; the point (400, 5) on camera cam1")


@pytest.mark.parametrize(
    ("answer", "match"),
    [
        (Answer(question_id="nope", text="x"), "no open question"),
        (Answer(question_id="q2"), "empty"),
        (Answer(question_id="q2", tap=TapPoint(x=1, y=1)), "not about a camera"),
        (
            Answer(
                question_id="q3",
                calibration=ClickCalibration(image_px=SQUARE_PX, ground_m=SQUARE_M),
            ),
            "does not ask for a calibration",
        ),
        (
            Answer(
                question_id="calibrate-1",
                calibration=ClickCalibration(
                    image_px=((0, 0), (1, 1), (2, 2), (3, 3)), ground_m=SQUARE_M
                ),
            ),
            "does not fit",
        ),
    ],
)
def test_unusable_answers_are_refused(answer: Answer, match: str) -> None:
    with pytest.raises(AnswerError, match=match):
        apply_answers(inputs(), [SCHEDULE, EXIT, CALIBRATE], [answer])


def test_a_question_answered_twice_is_refused() -> None:
    twice = [Answer(question_id="q2", text="a"), Answer(question_id="q2", text="b")]
    with pytest.raises(AnswerError, match="answered twice"):
        apply_answers(inputs(), [SCHEDULE], twice)


def test_answer_text_is_bounded_and_plain() -> None:
    with pytest.raises(ValueError, match="at most 500"):
        Answer(question_id="q2", text="x" * 501)
    with pytest.raises(ValueError, match="pattern"):
        Answer(question_id="q2", text="line\nbreak")
    with pytest.raises(ValueError, match="pattern"):
        Answer(question_id="../x", text="a")


def test_saved_questions_answers_and_zones(tmp_path: Path) -> None:
    store = PlanStore(tmp_path)
    with pytest.raises(PipelineError):
        saved_questions(store, "p1")
    store.save_input(inputs().model_copy(update={"zones": (DOOR,)}))
    assert saved_questions(store, "p1") == ()
    store.save_step(
        "p1", "ask", AskResult(questions=(Question(**SCHEDULE.model_dump(exclude={"step"})),))
    )
    assert saved_questions(store, "p1") == (SCHEDULE,)
    sink = DOOR.model_copy(update={"zone_id": "sink"})
    store.save_step("p1", "probe", ProbeResult(needs={}, probes=(), zones=(sink,)))
    assert plan_zones(store, store.load_input("p1")) == (DOOR, sink)
    assert saved_answers(store, "p1") == []
    record = AnswerRecord(at=EPOCH, plan_version=1, answers=(Answer(question_id="q2", text="a"),))
    record_answers(store, "p1", record)
    record_answers(store, "p1", record)
    assert saved_answers(store, "p1") == [record, record]
    (tmp_path / "p1" / "answers.json").write_text("not json", encoding="utf-8")
    assert saved_answers(store, "p1") == []


def wait_until_idle(service: PlannerService, plan_id: str) -> None:
    deadline = time.monotonic() + 10
    while service.questions(plan_id).running:
        assert time.monotonic() < deadline, "the planner run did not finish"
        time.sleep(0.01)


def test_answering_re_runs_the_plan_one_version_up(tmp_path: Path) -> None:
    frame = tmp_path / "cam1.jpg"
    frame.write_bytes(b"\xff\xd8\xff")
    finished: list[SitePlan] = []
    gate = threading.Event()

    def on_plan(plan: SitePlan) -> None:
        assert gate.wait(10)
        finished.append(plan)

    service = PlannerService(
        _pipeline(tmp_path, _spark()),
        site=SiteConfig(site_name="Main St kitchen", roles={"shift_lead": "web_push"}),
        frames=lambda: {"cam1": [frame, tmp_path / "missing.jpg"]},
        on_plan=on_plan,
        new_id=lambda: "plan-abc",
        clock=FakeClock(),
    )
    plan_id = service.submit([RequestItem(kind="text", text=HANDWASH)])
    with pytest.raises(PlanBusyError):  # the first run is held at `on_plan`
        service.answer(plan_id, [Answer(question_id="q2", text="6-22")])
    gate.set()
    wait_until_idle(service, plan_id)

    asked = service.questions(plan_id)
    assert [q.question_id for q in asked.questions] == ["q2"]  # roles are known: q1 dropped
    assert asked.answered == 0
    assert service.frame(plan_id, "cam1") == frame  # the newest frame that still exists
    assert service.frame(plan_id, "cam9") is None
    with pytest.raises(AnswerError):
        service.answer(plan_id, [Answer(question_id="q1", text="x")])
    assert service.questions(plan_id).running is False  # a refused answer queues nothing

    service.answer(plan_id, [Answer(question_id="q2", text="6am to 10pm")])
    wait_until_idle(service, plan_id)
    service.shutdown()

    assert [p.version for p in finished] == [1, 2]
    rerun = PlanStore(tmp_path / "plans").load_input(plan_id)
    assert rerun.request[-1].text is not None
    assert rerun.request[-1].text.endswith("6am to 10pm")
    assert service.questions(plan_id).answered == 1
    assert saved_answers(PlanStore(tmp_path / "plans"), plan_id)[0].plan_version == 1
