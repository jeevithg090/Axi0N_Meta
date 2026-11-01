"""`POST /correction`: the correction loop over HTTP (mounted by P3-08's API app)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from fastapi import APIRouter, HTTPException, params, status
from starlette.concurrency import run_in_threadpool

from rulecam.bus import Topic
from rulecam.perception.correction import (
    CorrectionError,
    CorrectionRequest,
    CorrectionResult,
    CorrectionService,
    UnknownEventError,
)


class Publisher(Protocol):
    """Anything that publishes on bus topics (`rulecam.bus.Bus`)."""

    def publish(self, topic: Topic, payload: object) -> None:
        """Deliver `payload` to subscribers of `topic`."""
        ...


def correction_router(
    service: CorrectionService,
    bus: Publisher,
    *,
    dependencies: Sequence[params.Depends],
) -> APIRouter:
    """Build the router; segmentation runs in a worker thread, publishing on the event loop.

    A correction cancels candidates, writes `label` records and triggers SAM calls, so the caller
    must pass its authentication explicitly (the API app passes the owner check).

    Args:
        service: The correction service.
        bus: Receives the cancelled candidate on `candidate.new`.
        dependencies: Authentication applied to every route.

    Returns:
        An `APIRouter` with `POST /correction`.
    """
    router = APIRouter(tags=["correction"], dependencies=list(dependencies))

    @router.post("/correction")
    async def post_correction(request: CorrectionRequest) -> CorrectionResult:
        """Apply a negative-point correction to an event and re-evaluate its rule."""
        try:
            result = await run_in_threadpool(service.correct, request)
        except UnknownEventError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except CorrectionError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if result.status == "cancelled":
            bus.publish(Topic.CANDIDATE_NEW, result.candidate)
        return result

    return router
