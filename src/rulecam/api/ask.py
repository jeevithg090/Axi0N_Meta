"""`POST /ask`: an owner's free-form question about what the cameras saw (`rulecam.ask`)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from rulecam.ask import AskAgent, AskAnswer

HTTP_UNAVAILABLE = 503


class AskRequest(BaseModel):
    """The owner's question."""

    question: str = Field(min_length=1, max_length=2000)


def ask_router(agent: AskAgent | None, dependencies: Sequence[Any] = ()) -> APIRouter:
    """The `/ask` route; 503 when the agent could not be built (no Spark key).

    Args:
        agent: The question-answering agent, or `None`.
        dependencies: Route dependencies (the owner check).

    Returns:
        The router.
    """
    router = APIRouter(dependencies=list(dependencies))

    @router.post("/ask", response_model=AskAnswer, tags=["ask"])
    def ask(body: AskRequest) -> AskAnswer:
        """Answer with Spark, SAM 3.1 and Glimmer over the recorded tracks and video."""
        if agent is None:
            detail = "asking needs RULECAM_PROTOTYPE_MODE=true and Muse Spark (META_API_KEY)"
            raise HTTPException(HTTP_UNAVAILABLE, detail)
        return agent.ask(body.question)

    return router
