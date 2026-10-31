"""`/plans`: a minimal in-memory plan registry (create, list, get, approve) until P2 lands.

Plans are `SitePlan` contracts. Creating and approving are owner-only (loopback). The registry
holds a bounded number of plans.

Approving activates the plan (`rulecam.runtime.activate`): only rules whose acceptance trace
passes (or is amber) are enforced; a plan with no such rule is refused with 409 and the reasons,
and without a running runtime loop the approval is refused with 503. An activated plan is
persisted, handed to the runtime, recorded in the evidence chain (`plan`, `plan_approved`) and
published on `plan.updated`.

Planner intake (P3-09b): `POST /plans/uploads` stores a voice clip, photo, PDF or walk-through
under a content-addressed name, accepting only the file types the planner's Understand step
(`rulecam.planner.steps.understand.ALLOWED_SUFFIXES`) takes, checked by content type, by the
file's leading bytes and by size. `POST /plans/requests` hands the request items to the Site
Planner runner (P2-06), which reports progress on `planner.step`; a file item may only name an
upload (`uploads/<name>`, as returned by `POST /plans/uploads`), which is resolved to its stored
path, so a client never supplies a server path. Both are owner-only.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from rulecam.bus import Bus, Topic
from rulecam.capture.pair_phone import require_owner
from rulecam.contracts import RequestItem, SitePlan
from rulecam.planner.steps.understand import ALLOWED_SUFFIXES
from rulecam.runtime.activate import (
    NothingActivatableError,
    PlanActivator,
    RuleGate,
    RuntimeNotRunningError,
)

MAX_PLANS = 64
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
PlanId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
UploadKind = Literal["voice", "pdf", "photo", "video"]
# Content type -> (request item kind, stored extension). Only types the Understand step accepts.
UPLOAD_TYPES: dict[str, tuple[UploadKind, str]] = {
    "audio/wav": ("voice", "wav"),
    "audio/x-wav": ("voice", "wav"),
    "audio/wave": ("voice", "wav"),
    "image/jpeg": ("photo", "jpg"),
    "image/png": ("photo", "png"),
    "image/webp": ("photo", "webp"),
    "application/pdf": ("pdf", "pdf"),
    "video/mp4": ("video", "mp4"),
}
UPLOAD_PREFIX = "uploads/"
_UPLOAD_NAME = re.compile(r"[0-9a-f]{32}\.[a-z0-9]{2,4}")


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


class PlanApproved(PlanSummary):
    """Reply of `POST /plans/{plan_id}/approve`: what the runtime now enforces."""

    plan_version_hash: str
    activated: list[str]
    gates: list[RuleGate]


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
    kind: UploadKind
    bytes: int


def _upload_kind(content_type: str) -> tuple[UploadKind, str]:
    try:
        return UPLOAD_TYPES[content_type.split(";", maxsplit=1)[0].strip().lower()]
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"unsupported upload type {content_type!r}"
        ) from exc


def sniff_matches(ext: str, data: bytes) -> bool:
    """Whether the file's leading bytes are those of `ext` (the content type is client-chosen)."""
    if ext == "pdf":
        return data.startswith(b"%PDF-")
    if ext == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "jpg":
        return data.startswith(b"\xff\xd8\xff")
    if ext in {"webp", "wav"}:
        return data[:4] == b"RIFF" and data[8:12] == (b"WEBP" if ext == "webp" else b"WAVE")
    return ext == "mp4" and data[4:8] == b"ftyp"


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "upload is too large")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "upload is empty")
    return data


def resolve_upload(uploads_dir: Path, item: RequestItem) -> RequestItem:
    """Point a file item at its stored upload; text items pass through unchanged.

    Raises:
        HTTPException: 422 unless `item.file` is `uploads/<name>` naming a stored upload whose
            extension the item's kind accepts.
    """
    if item.kind == "text":
        return item
    name = (item.file or "").removeprefix(UPLOAD_PREFIX)
    path = uploads_dir / name
    if (
        not (item.file or "").startswith(UPLOAD_PREFIX)
        or _UPLOAD_NAME.fullmatch(name) is None
        or path.suffix not in ALLOWED_SUFFIXES[item.kind]
        or not path.is_file()
    ):
        msg = f"a {item.kind} item must name a file from POST /plans/uploads"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, msg)
    return item.model_copy(update={"file": str(path)})


def save_upload(uploads_dir: Path, data: bytes, ext: str) -> Path:
    """Store an upload under a content-addressed name (never a client-supplied path)."""
    uploads_dir.mkdir(parents=True, exist_ok=True)
    path = uploads_dir / f"{hashlib.sha256(data).hexdigest()[:32]}.{ext}"
    if not path.exists():
        atomic_write_bytes(path, data)
    return path


def approve(
    registry: PlanRegistry, bus: Bus, activator: PlanActivator | None, plan_id: str
) -> PlanApproved:
    """Activate a registered plan in the runtime, then mark it approved and publish it.

    Raises:
        HTTPException: 404 for an unknown plan, 409 when no rule passes the activation gate,
            503 when there is no activator or no running runtime loop.
    """
    plan, _ = registry.get(plan_id)
    if activator is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "plan activation is off")
    try:
        activation = activator.activate(plan)
    except NothingActivatableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except RuntimeNotRunningError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    registry.approve(plan_id)
    bus.publish(Topic.PLAN_UPDATED, plan)
    return PlanApproved(
        **_summary(plan, approved=True).model_dump(),
        plan_version_hash=activation.active_plan.plan_version_hash,
        activated=activation.rule_ids,
        gates=list(activation.gates),
    )


def plans_router(
    registry: PlanRegistry,
    bus: Bus,
    *,
    uploads_dir: Path | None = None,
    planner: PlannerRunner | None = None,
    activator: PlanActivator | None = None,
    max_upload_bytes: int = MAX_UPLOAD_BYTES,
) -> APIRouter:
    """Build the `/plans` router.

    Args:
        registry: Where plans are kept.
        bus: Receives `plan.updated` on approval.
        uploads_dir: Where `POST /plans/uploads` stores files; `None` disables uploads.
        planner: The Site Planner runner; `POST /plans/requests` answers 503 without one.
        activator: Puts approved plans into force; approval answers 503 without one.
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
        if not sniff_matches(ext, data):
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"the file is not a {ext} file"
            )
        path = save_upload(uploads_dir, data, ext)
        return UploadResult(file=f"{UPLOAD_PREFIX}{path.name}", kind=kind, bytes=len(data))

    @router.post("/requests", status_code=status.HTTP_202_ACCEPTED, dependencies=owner)
    def request_plan(body: PlanRequest) -> PlanRequestAccepted:
        if planner is None or uploads_dir is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "the Site Planner is not running yet"
            )
        items = [resolve_upload(uploads_dir, item) for item in body.items]
        return PlanRequestAccepted(plan_id=planner.submit(items))

    @router.get("")
    def list_plans() -> list[PlanSummary]:
        return registry.summaries()

    @router.post("", status_code=status.HTTP_201_CREATED, dependencies=owner)
    def create_plan(plan: SitePlan) -> PlanSummary:
        return registry.put(plan)

    @router.get("/{plan_id}")
    def get_plan(plan_id: PlanId) -> SitePlan:
        return registry.get(plan_id)[0]

    @router.post(
        "/{plan_id}/approve",
        dependencies=owner,
        responses={409: {"description": "No rule passes the activation gate"}},
    )
    def approve_plan(plan_id: PlanId) -> PlanApproved:
        return approve(registry, bus, activator, plan_id)

    return router
