"""`POST /crowd/scan`: density episodes on one camera, each checked on video (owner-only)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from rulecam.crowd.density import DEFAULT_MIN_SECONDS, Level
from rulecam.crowd.watch import CrowdError, CrowdReport, CrowdWatch

HTTP_UNPROCESSABLE = 422
HTTP_UNAVAILABLE = 503
MAX_WINDOW_S = 6 * 3600


class ScanRequest(BaseModel):
    """The camera and window to scan."""

    camera_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    start: datetime
    end: datetime
    at_least: Level = "crowded"
    min_seconds: float = Field(default=DEFAULT_MIN_SECONDS, ge=0.0, le=600.0)
    verify: bool = True


def crowd_scan_router(watch: CrowdWatch | None, *, dependencies: Sequence[Any] = ()) -> APIRouter:
    """The `/crowd` routes; 503 without a track store."""
    router = APIRouter(prefix="/crowd", tags=["crowd"], dependencies=list(dependencies))

    @router.post("/scan")
    async def scan(body: ScanRequest) -> CrowdReport:
        if watch is None:
            raise HTTPException(HTTP_UNAVAILABLE, "crowd watch needs the runtime's track store")
        if (body.end - body.start).total_seconds() > MAX_WINDOW_S:
            raise HTTPException(HTTP_UNPROCESSABLE, "scan at most 6 hours at a time")
        try:
            return await asyncio.to_thread(
                watch.scan,
                body.camera_id,
                body.start,
                body.end,
                at_least=body.at_least,
                min_seconds=body.min_seconds,
                verify=body.verify,
            )
        except CrowdError as exc:
            raise HTTPException(HTTP_UNPROCESSABLE, str(exc)) from exc

    return router
