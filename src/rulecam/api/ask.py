"""`/ask`: an owner's free-form questions about what the cameras saw (`rulecam.ask`).

- `POST /ask` answers in one JSON reply (`AskAnswer`).
- `POST /ask/stream` sends the same work as server-sent events, each step the moment it happens
  (`event: <type>` and `data: <AskEvent JSON>`), ending with `answer` (or `error`). A comment
  line every `HEARTBEAT_S` seconds keeps proxies from closing a long answer. When the client
  goes away, the agent starts no further turn.
- `GET /ask/frames/{name}` returns a frame a step showed a model (the UI's thumbnails).

A follow-up sends the `conversation_id` of the answer it builds on.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Sequence
from typing import Any, Final

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from rulecam.ask import AskAgent, AskAnswer
from rulecam.ask.trace import AskEvent
from rulecam.log import get_logger

_log = get_logger(__name__)

HTTP_NOT_FOUND = 404
HTTP_UNAVAILABLE = 503
HEARTBEAT_S: Final = 10.0
UNAVAILABLE: Final = "asking needs RULECAM_PROTOTYPE_MODE=true and Muse Spark (META_API_KEY)"
_STREAM_HEADERS: Final = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class AskRequest(BaseModel):
    """The owner's question, optionally a follow-up in a conversation."""

    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="The conversation_id of an earlier answer, to ask a follow-up.",
    )


def sse(event: AskEvent) -> str:
    """One server-sent event: its type as the event name, the event as JSON data."""
    return f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"


async def stream_answer(
    agent: AskAgent, body: AskRequest, *, heartbeat_s: float = HEARTBEAT_S
) -> AsyncIterator[str]:
    """Run the agent in a worker thread and yield its events as server-sent events.

    Args:
        agent: The agent.
        body: The question.
        heartbeat_s: Seconds of silence after which a comment line is sent.

    Yields:
        SSE text: every `AskEvent`, ending with `answer` or `error`.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[AskEvent | None] = asyncio.Queue()
    stop = threading.Event()

    def put(item: AskEvent | None) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:  # the event loop is gone (shutdown): nobody to tell
            stop.set()

    def work() -> None:
        try:
            agent.ask(body.question, conversation_id=body.conversation_id, emit=put, stop=stop)
        except Exception as exc:
            _log.exception("ask_stream_failed")
            put(AskEvent(type="error", detail=f"answering failed: {type(exc).__name__}"))
        finally:
            put(None)

    worker = loop.run_in_executor(None, work)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), heartbeat_s)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                break
            yield sse(event)
    finally:
        stop.set()
        if worker.done():
            await worker


def ask_router(agent: AskAgent | None, dependencies: Sequence[Any] = ()) -> APIRouter:
    """The `/ask` routes; 503 when the agent could not be built (no Spark key).

    Args:
        agent: The question-answering agent, or `None`.
        dependencies: Route dependencies (the owner check).

    Returns:
        The router.
    """
    router = APIRouter(dependencies=list(dependencies))

    def ready() -> AskAgent:
        if agent is None:
            raise HTTPException(HTTP_UNAVAILABLE, UNAVAILABLE)
        return agent

    @router.post("/ask", response_model=AskAnswer, tags=["ask"])
    def ask(body: AskRequest) -> AskAnswer:
        """Answer with Spark, SAM 3.1 and Glimmer over the recorded tracks and video."""
        return ready().ask(body.question, conversation_id=body.conversation_id)

    @router.post(
        "/ask/stream",
        tags=["ask"],
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}, "description": "AskEvent stream"}},
    )
    def ask_stream(body: AskRequest) -> StreamingResponse:
        """The same answer, streamed: each step as a server-sent `AskEvent` as it happens."""
        return StreamingResponse(
            stream_answer(ready(), body), media_type="text/event-stream", headers=_STREAM_HEADERS
        )

    @router.get("/ask/frames/{name}", tags=["ask"], response_class=FileResponse)
    def ask_frame(name: str) -> FileResponse:
        """A frame a step showed SAM 3.1 or Glimmer (JPEG), by the name in `AskStep.frames`."""
        path = ready().frame_file(name)
        if path is None:
            raise HTTPException(HTTP_NOT_FOUND, "no such frame")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private"})

    return router
