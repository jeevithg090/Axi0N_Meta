"""What Spark asks for in a turn, and the trace of everything done to answer a question."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from rulecam.contracts import Frozen

Action = Literal["sql", "count_objects", "look", "answer"]


class AskAction(Frozen):
    """One action of a turn: a tool call, or the final answer."""

    thought: str = Field(description="One sentence: why this step.")
    action: Action
    sql: str | None = Field(
        default=None,
        description="For sql: one or more SELECT/WITH statements separated by ';'.",
    )
    concept: str | None = Field(default=None, description="For count_objects: a noun phrase.")
    camera_id: str | None = Field(default=None, description="For count_objects and look.")
    times: list[str] = Field(
        default_factory=list, description="For count_objects and look: ISO-8601 UTC instants."
    )
    question: str | None = Field(default=None, description="For look: what Glimmer should answer.")
    marks: bool = Field(
        default=True, description="For look: draw the tracked objects as numbered boxes."
    )
    answer: str | None = Field(default=None, description="For answer: the reply to the owner.")
    numbers: list[float | None] = Field(
        default_factory=list,
        description=(
            "For answer: the numbers the reply gives, in the order the question asks for them "
            "(e.g. [5, 1, 4] for counts at three times; null for one you could not get)."
        ),
    )


class AskTurn(Frozen):
    """One turn of the model: tool calls that run at the same time, or the final answer."""

    actions: list[AskAction] = Field(
        min_length=1,
        description=(
            "The tool calls to run now, all at the same time (none sees another's result), or "
            "one answer action on its own."
        ),
    )


class AskStep(Frozen):
    """What one action did, for the trace.

    Attributes:
        turn: The turn whose actions it ran with (0-based); a turn's actions ran at once.
        action: The action.
        result: Its result (`None` for the answer).
        seconds: How long it took.
        model: The model that ran it (`None` for SQL and the answer).
        frames: The frames it showed a model, by file name (`GET /ask/frames/{name}`).
    """

    turn: int = 0
    action: AskAction
    result: object
    seconds: float
    model: str | None = None
    frames: list[str] = Field(default_factory=list)


class AskTurnTiming(Frozen):
    """How long Spark took to choose one turn's actions.

    Attributes:
        turn: The turn (0-based).
        model: The model that chose them.
        seconds: Spark's time for the turn.
        actions: How many actions it chose.
    """

    turn: int
    model: str
    seconds: float
    actions: int


class AskReview(Frozen):
    """A second model's check of a draft answer against its trace (add-on 2.7).

    Attributes:
        verdict: `accept`, `revise` (sent back to Spark once), or `error` (the review failed and
            the draft stood).
        problems: What the reviewer found wrong or missing.
        advice: What it told Spark to check or change.
        draft: The draft it reviewed.
        turn: The turn whose answer it reviewed.
        model: The reviewing model.
        seconds: How long the review took.
        reasoning_ref: Id of the reviewer's stored reasoning (`data/reasoning/<id>.txt`).
        detail: Why the review failed (for `error`).
    """

    verdict: Literal["accept", "revise", "error"]
    problems: list[str] = Field(default_factory=list)
    advice: str = ""
    draft: str = ""
    turn: int = 0
    model: str = ""
    seconds: float = 0.0
    reasoning_ref: str | None = None
    detail: str | None = None


class AskAnswer(Frozen):
    """The answer to an owner's question, with every step that led to it.

    Attributes:
        question: The owner's question.
        answer: The reply.
        steps: Every action, in turn order (a turn's actions ran at once).
        seconds: Wall-clock seconds for the whole answer.
        models: Every model used, Spark first.
        conversation_id: Send it with the next question to ask a follow-up.
        turns: Spark's time per turn.
        numbers: The numbers the answer gives, in the order asked (for grading without prose).
        prompt_sha256: SHA-256 of the instructions the models got (prompt and schema).
        receipt: SHA-256 of this answer's row in the hash-chained run log (empty if unlogged).
        reviews: The second model's checks of the drafts, in order (empty when off).
    """

    question: str
    answer: str
    steps: list[AskStep]
    seconds: float
    models: list[str]
    conversation_id: str = ""
    turns: list[AskTurnTiming] = Field(default_factory=list)
    numbers: list[float | None] = Field(default_factory=list)
    prompt_sha256: str = ""
    receipt: str = ""
    reviews: list[AskReview] = Field(default_factory=list)


EventType = Literal[
    "start", "thinking", "actions", "step", "reviewing", "review", "answer", "error"
]


class AskEvent(Frozen):
    """One thing that happened while answering, sent as it happens (`POST /ask/stream`).

    - `start`: the question was taken (`conversation_id`);
    - `thinking`: Spark is choosing turn `turn`'s actions;
    - `actions`: it chose `actions` in `seconds`;
    - `step`: action `index` of turn `turn` finished (`step`); a turn's steps can finish in any
      order;
    - `reviewing`: a second model is checking turn `turn`'s draft answer;
    - `review`: its `review` (a `revise` sends the draft back: more turns follow);
    - `answer`: the final `answer`, with the whole trace;
    - `error`: answering failed (`detail`).
    """

    type: EventType
    conversation_id: str | None = None
    turn: int | None = None
    index: int | None = None
    seconds: float | None = None
    actions: list[AskAction] = Field(default_factory=list)
    step: AskStep | None = None
    answer: AskAnswer | None = None
    review: AskReview | None = None
    detail: str | None = None
