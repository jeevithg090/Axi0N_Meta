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
    """

    turn: int = 0
    action: AskAction
    result: object
    seconds: float
    model: str | None = None


class AskAnswer(Frozen):
    """The answer to an owner's question, with every step that led to it."""

    question: str
    answer: str
    steps: list[AskStep]
    seconds: float
    models: list[str]
