"""`/shelf`: supermarket shelf gaps on a camera's frame or an uploaded photo (owner-only).

- `POST /shelf/scan` scans `camera_id`'s recorded frame at `at`.
- `POST /shelf/scan-image` scans an uploaded JPEG or PNG (a staff phone photo of a bay).
- `GET /shelf/marked/{name}` returns a marked image a report names.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from rulecam.clients.errors import ModelClientError
from rulecam.shelf.gaps import ShelfError, ShelfReport, ShelfScanner

HTTP_NOT_FOUND = 404
HTTP_TOO_LARGE = 413
HTTP_UNPROCESSABLE = 422
HTTP_BAD_GATEWAY = 502
HTTP_UNAVAILABLE = 503
MAX_UPLOAD_BYTES: Final = 15 * 1024 * 1024
SUFFIXES: Final = {"image/jpeg": ".jpg", "image/png": ".png"}
UNAVAILABLE: Final = "shelf scans need Muse Spark and SAM 3.1 (META_API_KEY)"


class ShelfScanRequest(BaseModel):
    """Which camera frame to scan."""

    camera_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    at: datetime


async def _run(scan: Callable[[], ShelfReport]) -> ShelfReport:
    """Run a scan off the event loop; map its errors to HTTP answers."""
    try:
        return await asyncio.to_thread(scan)
    except ShelfError as exc:
        raise HTTPException(HTTP_UNPROCESSABLE, str(exc)) from exc
    except ModelClientError as exc:
        raise HTTPException(HTTP_BAD_GATEWAY, f"model call failed: {exc}") from exc


async def _save(image: UploadFile, uploads_dir: Path) -> Path:
    """Store an uploaded JPEG or PNG under a fresh name."""
    suffix = SUFFIXES.get(image.content_type or "")
    if suffix is None:
        raise HTTPException(HTTP_UNPROCESSABLE, "send a JPEG or PNG")
    data = await image.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(HTTP_TOO_LARGE, "images up to 15 MB")
    uploads_dir.mkdir(parents=True, exist_ok=True)
    path = uploads_dir / f"shelf-{uuid.uuid4().hex[:16]}{suffix}"
    path.write_bytes(data)
    return path


def shelf_router(
    scanner: ShelfScanner | None,
    *,
    uploads_dir: Path,
    marked_dir: Path,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    """The `/shelf` routes; 503 without the models."""
    router = APIRouter(prefix="/shelf", tags=["shelf"], dependencies=list(dependencies))

    def _scanner() -> ShelfScanner:
        if scanner is None:
            raise HTTPException(HTTP_UNAVAILABLE, UNAVAILABLE)
        return scanner

    @router.post("/scan")
    async def scan(body: ShelfScanRequest) -> ShelfReport:
        svc = _scanner()
        return await _run(lambda: svc.scan_camera(body.camera_id, body.at))

    @router.post("/scan-image")
    async def scan_image(image: Annotated[UploadFile, File()]) -> ShelfReport:
        svc = _scanner()
        path = await _save(image, uploads_dir)
        return await _run(lambda: svc.scan_image(path))

    @router.get("/marked/{name}")
    def marked_image(name: str) -> FileResponse:
        path = marked_dir / name
        if Path(name).name != name or path.suffix != ".jpg" or not path.is_file():
            raise HTTPException(HTTP_NOT_FOUND, "no such image")
        return FileResponse(path, media_type="image/jpeg")

    return router
