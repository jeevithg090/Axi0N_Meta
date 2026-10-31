"""`/plans`: a minimal in-memory plan registry (create, list, get, approve) until P2 lands.

Plans are `SitePlan` contracts. Creating and approving are owner-only (loopback); approving
publishes `plan.updated`. The registry holds a bounded number of plans.

Planner intake (P3-09b): `POST /plans/uploads` stores a voice clip, photo, PDF or walk-through
under a content-addressed name (type and size checked, never a client path), and
`POST /plans/requests` hands the request items to the Site Planner runner (P2-06), which reports
progress on `planner.step`. Both are owner-only.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from rulecam.bus import Bus, Topic
from rulecam.capture.pair_phone import require_owner
from rulecam.contracts import RequestItem, SitePlan

MAX_PLANS = 64
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
PlanId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
UPLOAD_TYPES: dict[str, tuple[str, str]] = {
    "audio/webm": ("voice", "webm"),
    "audio/ogg": ("voice", "ogg"),
    "audio/wav": ("voice", "wav"),
    "audio/x-wav": ("voice", "wav"),
    "audio/mp4": ("voice", "m4a"),
    "audio/mpeg": ("voice", "mp3"),
    "image/jpeg": ("photo", "jpg"),
    "image/png": ("photo", "png"),
    "application/pdf": ("pdf", "pdf"),
    "video/mp4": ("video", "mp4"),
    "video/quicktime": ("video", "mov"),
}


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `path` via a temporary file and `os.replace`."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class PlanSummary(BaseModel):
    """One row of `GET /plans`."""

    plan_id: str
    version: int
    site_name: str
    approved: bool


class PlanRegistry:
    """Thread-safe, bounded store of submitted plans and their approval state."""

    def __init__(self, max_plans: int = MAX_PLANS) -> None:
        """Create an empty registry holding at most `max_plans` plans."""
        self._max = max_plans
        self._plans: dict[str, tuple[SitePlan, bool]] = {}
        self._lock = threading.Lock()

    def put(self, plan: SitePlan) -> PlanSummary:
        """Store (or replace, un-approving) a plan.

        Raises:
            HTTPException: 409 when the registry is full.
        """
        with self._lock:
            if plan.plan_id not in self._plans and len(self._plans) >= self._max:
                raise HTTPException(status.HTTP_409_CONFLICT, "plan registry is full")
            self._plans[plan.plan_id] = (plan, False)
        return _summary(plan, approved=False)

    def get(self, plan_id: str) -> tuple[SitePlan, bool]:
        """Return a plan and whether it is approved.

        Raises:
            HTTPException: 404 for an unknown plan.
        """
        with self._lock:
            found = self._plans.get(plan_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no plan {plan_id!r}")
        return found

    def approve(self, plan_id: str) -> SitePlan:
        """Mark a plan approved and return it (404 if unknown)."""
        plan, _ = self.get(plan_id)
        with self._lock:
            self._plans[plan_id] = (plan, True)
        return plan

    def summaries(self) -> list[PlanSummary]:
        """Return every plan, in submission order."""
        with self._lock:
            items = list(self._plans.values())
        return [_summary(plan, approved=ok) for plan, ok in items]


def _summary(plan: SitePlan, *, approved: bool) -> PlanSummary:
    return PlanSummary(
        plan_id=plan.plan_id, version=plan.version, site_name=plan.site_name, approved=approved
    )


class PlannerRunner(Protocol):
    """Starts the fixed-step Site Planner (P2-06) on a request; it publishes `planner.step`."""

    def submit(self, items: Sequence[RequestItem]) -> str:
        """Queue a planning run and return its plan id."""
        ...


class PlanRequest(BaseModel):
    """Body of `POST /plans/requests`: the owner's request, in any mix of formats."""

    items: list[RequestItem] = Field(min_length=1, max_length=20)


class PlanRequestAccepted(BaseModel):
    """Reply of `POST /plans/requests`."""

    plan_id: str


class UploadResult(BaseModel):
    """Reply of `POST /plans/uploads`: what to put in `RequestItem.file` and `.kind`."""

    file: str
    kind: str
    bytes: int


def _upload_kind(content_type: str) -> tuple[str, str]:
    try:
        return UPLOAD_TYPES[content_type.split(";", maxsplit=1)[0].strip().lower()]
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"unsupported upload type {content_type!r}"
        ) from exc


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "upload is too large")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "upload is empty")
    return data


def save_upload(uploads_dir: Path, data: bytes, ext: str) -> Path:
    """Store an upload under a content-addressed name (never a client-supplied path)."""
    uploads_dir.mkdir(parents=True, exist_ok=True)
    path = uploads_dir / f"{hashlib.sha256(data).hexdigest()[:32]}.{ext}"
    if not path.exists():
        atomic_write_bytes(path, data)
    return path


def plans_router(
    registry: PlanRegistry,
    bus: Bus,
    *,
    uploads_dir: Path | None = None,
    planner: PlannerRunner | None = None,
    max_upload_bytes: int = MAX_UPLOAD_BYTES,
) -> APIRouter:
    """Build the `/plans` router.

    Args:
        registry: Where plans are kept.
        bus: Receives `plan.updated` on approval.
        uploads_dir: Where `POST /plans/uploads` stores files; `None` disables uploads.
        planner: The Site Planner runner; `POST /plans/requests` answers 503 without one.
        max_upload_bytes: Upload size cap (the Voice Transcribe file limit by default).

    Returns:
        The router.
    """
    router = APIRouter(prefix="/plans", tags=["plans"])
    owner = [Depends(require_owner)]

    @router.post("/uploads", status_code=status.HTTP_201_CREATED, dependencies=owner)
    async def upload(file: UploadFile) -> UploadResult:
        if uploads_dir is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "uploads are disabled")
        kind, ext = _upload_kind(file.content_type or "")
        data = await _read_limited(file, max_upload_bytes)
        path = save_upload(uploads_dir, data, ext)
        return UploadResult(file=f"uploads/{path.name}", kind=kind, bytes=len(data))

    @router.post("/requests", status_code=status.HTTP_202_ACCEPTED, dependencies=owner)
    def request_plan(body: PlanRequest) -> PlanRequestAccepted:
        if planner is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "the Site Planner is not running yet"
            )
        return PlanRequestAccepted(plan_id=planner.submit(body.items))

    @router.get("")
    def list_plans() -> list[PlanSummary]:
        return registry.summaries()

    @router.post("", status_code=status.HTTP_201_CREATED, dependencies=owner)
    def create_plan(plan: SitePlan) -> PlanSummary:
        return registry.put(plan)

    @router.get("/{plan_id}")
    def get_plan(plan_id: PlanId) -> SitePlan:
        return registry.get(plan_id)[0]

    @router.post("/{plan_id}/approve", dependencies=owner)
    def approve_plan(plan_id: PlanId) -> PlanSummary:
        plan = registry.approve(plan_id)
        bus.publish(Topic.PLAN_UPDATED, plan)
        return _summary(plan, approved=True)

    return router
