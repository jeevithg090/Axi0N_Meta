"""The owner's answers to the planner's questions, fed back into a re-run (WS4).

A run saves two kinds of question: step 5's clarifying questions (`05_ask.json`, missing plan
fields such as the schedule or which door is the exit) and step 8's (`08_accept.json`: a rule the
acceptance checks took out, or a camera whose metric rules need a known distance on its floor).
`saved_questions` lists them; `apply_answers` turns answers into the next run's inputs:

- **words** (or a chosen option) become a text request item, "Answer to the planner's question
  "...": ...", so step 1 reads them with the rest of the request;
- **a tap** on the question's camera frame is described in words, naming the smallest zone that
  contains it (`map_tap_to_zone`; never a person);
- **a calibration** (four or more tapped floor points and their positions in metres, e.g. the
  corners of a 1 m square) is fitted with `calibrate_clicks` and becomes the owner's calibration
  for that camera, which wins over anything the scene gave.

The re-run keeps the plan id and the saved camera frames (the taps were made on them), so the
plan comes back one version up and needs approving again. Answers are kept in
`plans/<plan_id>/answers.json`, newest last.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from rulecam.contracts import RequestItem
from rulecam.contracts.core import Calibration, Frozen, Zone
from rulecam.errors import RulecamError
from rulecam.perception.calibration import calibrate_clicks
from rulecam.planner.errors import PlannerError
from rulecam.planner.pipeline import (
    AcceptResult,
    AskResult,
    PipelineInput,
    PlanStore,
    ProbeResult,
    check_plan_id,
)
from rulecam.planner.site_config import ClickCalibration
from rulecam.planner.steps.ask import FrameTapAnswer, Question, map_tap_to_zone
from rulecam.replay.cache import atomic_write_text

MAX_ANSWER_CHARS: Final = 500
MAX_ANSWERS: Final = 10
ANSWERS_FILE: Final = "answers.json"

QuestionStep = Literal["ask", "accept"]


class AnswerError(PlannerError):
    """An answer that cannot be used (unknown question, nothing given, a calibration that fails)."""


class PlanBusyError(PlannerError):
    """The plan is being planned or re-planned right now; answer when that run has finished."""


class PlanQuestion(Frozen):
    """A saved question and the step that asked it."""

    step: QuestionStep
    question_id: str
    text: str
    target_field: str
    requires_frame_tap: bool = False
    camera_id: str | None = None
    options: tuple[str, ...] = ()


class TapPoint(BaseModel):
    """A tapped point in the pixels of the question camera's frame."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: float = Field(ge=0, le=100_000)
    y: float = Field(ge=0, le=100_000)


class Answer(BaseModel):
    """The owner's answer to one question: words, a tap, a floor calibration, or a mix."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    question_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    text: str = Field(default="", max_length=MAX_ANSWER_CHARS, pattern=r"^[^\x00-\x1f\x7f]*$")
    tap: TapPoint | None = None
    calibration: ClickCalibration | None = None


class AnswerRecord(BaseModel):
    """One batch of answers as saved in `answers.json`."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    plan_version: int | None
    answers: tuple[Answer, ...]


class PlanQuestions(BaseModel):
    """Body of `GET /plans/{plan_id}/questions`.

    Attributes:
        plan_id: The plan.
        questions: The last run's questions (empty once everything is answered).
        running: Whether a run of this plan is queued or running (answers wait for it).
        answered: How many batches of answers the owner has given so far.
    """

    plan_id: str
    questions: tuple[PlanQuestion, ...]
    running: bool
    answered: int


_RECORDS = TypeAdapter(list[AnswerRecord])


def _from(step: QuestionStep, question: Question) -> PlanQuestion:
    return PlanQuestion(step=step, **question.model_dump())


def saved_questions(store: PlanStore, plan_id: str) -> tuple[PlanQuestion, ...]:
    """The questions of a plan's last run: step 5's, then step 8's.

    Raises:
        PipelineError: If the plan has no saved run, or a step output is invalid.
    """
    store.load_input(plan_id)
    ask = store.load_step(plan_id, "ask", AskResult)
    accept = store.load_step(plan_id, "accept", AcceptResult)
    return (
        *(_from("ask", q) for q in (ask.questions if ask else ())),
        *(_from("accept", q) for q in (accept.questions if accept else ())),
    )


def plan_zones(store: PlanStore, inputs: PipelineInput) -> tuple[Zone, ...]:
    """The zones a tap can name: the owner's, then those the run's probe found in the scene."""
    probe = store.load_step(inputs.plan_id, "probe", ProbeResult)
    return (*inputs.zones, *(probe.zones if probe else ()))


def _tap_words(question: PlanQuestion, tap: TapPoint, zones: Sequence[Zone]) -> str:
    if question.camera_id is None:
        msg = f"question {question.question_id} is not about a camera; answer it in words"
        raise AnswerError(msg)
    where = f"the point ({tap.x:.0f}, {tap.y:.0f}) on camera {question.camera_id}"
    zone = map_tap_to_zone(FrameTapAnswer(camera_id=question.camera_id, x=tap.x, y=tap.y), zones)
    return f"{where}, inside zone {zone.zone_id}" if zone is not None else where


def _calibrate(question: PlanQuestion, calibration: ClickCalibration) -> tuple[str, Calibration]:
    if question.target_field != "calibration" or question.camera_id is None:
        msg = f"question {question.question_id} does not ask for a calibration"
        raise AnswerError(msg)
    try:
        fitted = calibrate_clicks(question.camera_id, calibration.image_px, calibration.ground_m)
    except RulecamError as exc:
        msg = f"the calibration for {question.camera_id} does not fit: {exc}"
        raise AnswerError(msg) from exc
    return question.camera_id, fitted


def _words(question: PlanQuestion, answer: Answer, zones: Sequence[Zone]) -> str | None:
    parts = [answer.text] if answer.text else []
    if answer.tap is not None:
        parts.append(_tap_words(question, answer.tap, zones))
    if not parts:
        return None
    return f'Answer to the planner\'s question "{question.text}": {"; ".join(parts)}'


def apply_answers(
    inputs: PipelineInput,
    questions: Sequence[PlanQuestion],
    answers: Sequence[Answer],
    zones: Sequence[Zone] = (),
) -> PipelineInput:
    """The next run's inputs: the request plus the answers in words, and any new calibrations.

    Args:
        inputs: The saved run's inputs.
        questions: Its questions (`saved_questions`).
        answers: The owner's answers, at most one per question.
        zones: Zones a tap may name (`plan_zones`).

    Returns:
        The inputs for the re-run (same plan id and frames).

    Raises:
        AnswerError: For an unknown or repeated question, an empty answer, a tap on a question
            without a camera, or a calibration that does not fit.
    """
    by_id = {q.question_id: q for q in questions}
    seen: set[str] = set()
    items: list[RequestItem] = []
    calibrations = dict(inputs.calibrations)
    for answer in answers:
        question = by_id.get(answer.question_id)
        if question is None or answer.question_id in seen:
            msg = f"no open question {answer.question_id!r} (or answered twice)"
            raise AnswerError(msg)
        seen.add(answer.question_id)
        words = _words(question, answer, zones)
        if words is None and answer.calibration is None:
            msg = f"the answer to {answer.question_id} is empty"
            raise AnswerError(msg)
        if words is not None:
            items.append(RequestItem(kind="text", text=words))
        if answer.calibration is not None:
            camera_id, fitted = _calibrate(question, answer.calibration)
            calibrations[camera_id] = fitted
    return inputs.model_copy(
        update={"request": (*inputs.request, *items), "calibrations": calibrations}
    )


def saved_answers(store: PlanStore, plan_id: str) -> list[AnswerRecord]:
    """Every batch of answers given for a plan, oldest first (empty when none or unreadable)."""
    path = store.root / check_plan_id(plan_id) / ANSWERS_FILE
    if not path.is_file():
        return []
    try:
        return _RECORDS.validate_json(path.read_text(encoding="utf-8"))
    except ValidationError:
        return []


def record_answers(store: PlanStore, plan_id: str, record: AnswerRecord) -> None:
    """Append one batch of answers to `plans/<plan_id>/answers.json`."""
    path = store.root / check_plan_id(plan_id) / ANSWERS_FILE
    records = [*saved_answers(store, plan_id), record]
    atomic_write_text(path, _RECORDS.dump_json(records, indent=2).decode("utf-8"))


__all__ = [
    "Answer",
    "AnswerError",
    "AnswerRecord",
    "PlanBusyError",
    "PlanQuestion",
    "PlanQuestions",
    "TapPoint",
    "apply_answers",
    "plan_zones",
    "record_answers",
    "saved_answers",
    "saved_questions",
]
