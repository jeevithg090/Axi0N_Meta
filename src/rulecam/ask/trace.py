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
    """

    question: str
    answer: str
    steps: list[AskStep]
    seconds: float
    models: list[str]
    conversation_id: str = ""
    turns: list[AskTurnTiming] = Field(default_factory=list)


EventType = Literal["start", "thinking", "actions", "step", "answer", "error"]


class AskEvent(Frozen):
    """One thing that happened while answering, sent as it happens (`POST /ask/stream`).

    - `start`: the question was taken (`conversation_id`);
    - `thinking`: Spark is choosing turn `turn`'s actions;
    - `actions`: it chose `actions` in `seconds`;
    - `step`: action `index` of turn `turn` finished (`step`); a turn's steps can finish in any
      order;
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
    detail: str | None = None
