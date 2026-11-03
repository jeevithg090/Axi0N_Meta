"""`/context`: the live site context (owner-only; each refresh is a paid, search-grounded call).

- `GET /context/{vertical}` returns the last refresh (404 before the first).
- `POST /context/{vertical}` refreshes it now, optionally for another location or with the
  owner's notes (the event's name, the store's street).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from rulecam.clients.errors import ModelClientError
from rulecam.context.site_context import (
    ContextError,
    SiteContext,
    SiteContextService,
    SiteLocation,
    Vertical,
)

HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422
HTTP_BAD_GATEWAY = 502
HTTP_UNAVAILABLE = 503
UNAVAILABLE = "site context needs Muse Spark (META_API_KEY)"


class RefreshRequest(BaseModel):
    """Optional overrides for one refresh."""

    location: SiteLocation | None = None
    notes: str = Field(default="", max_length=500)


def context_router(
    service: SiteContextService | None, *, dependencies: Sequence[Any] = ()
) -> APIRouter:
    """The `/context` routes; every one answers 503 without a service."""
    router = APIRouter(prefix="/context", tags=["context"], dependencies=list(dependencies))

    def _service() -> SiteContextService:
        if service is None:
            raise HTTPException(HTTP_UNAVAILABLE, UNAVAILABLE)
        return service

    @router.get("/{vertical}")
    def latest(vertical: Vertical) -> SiteContext:
        found = _service().latest(vertical)
        if found is None:
            raise HTTPException(HTTP_NOT_FOUND, f"no {vertical} context yet; POST to refresh it")
        return found

    @router.post("/{vertical}")
    async def refresh(vertical: Vertical, body: RefreshRequest | None = None) -> SiteContext:
        svc = _service()
        request = body or RefreshRequest()
        try:
            return await asyncio.to_thread(
                svc.refresh, vertical, location=request.location, notes=request.notes
            )
        except ContextError as exc:
            raise HTTPException(HTTP_UNPROCESSABLE, str(exc)) from exc
        except ModelClientError as exc:
            raise HTTPException(HTTP_BAD_GATEWAY, f"Muse Spark failed: {exc}") from exc

    return router
