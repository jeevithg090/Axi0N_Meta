"""`/connectors`: connect any camera in 30 seconds (every route owner-only).

| Route | What |
|---|---|
| `GET /connectors/cameras` | every connector camera with health and survey |
| `POST /connectors/cameras` | add one by URL: RTSP, HLS, MJPEG/HTTP, RTMP, public page, clip |
| `POST /connectors/cameras/upload` | upload a clip; it loops forever as a virtual camera |
| `GET /connectors/cameras/{id}` | one camera |
| `DELETE /connectors/cameras/{id}` | stop and forget it (chunks stay on disk) |
| `POST /connectors/cameras/{id}/restart` | reconnect now |
| `GET /connectors/snapshot/{camera_id}.jpg` | newest frame of any camera (phones too) |
| `POST /connectors/survey/{camera_id}` | (re)run the first look on any camera |
| `GET /connectors/survey/{camera_id}` | its survey |
| `GET /connectors/images/{name}` | a snapshot or marked image a survey names |
| `POST /connectors/scan` | ONVIF WS-Discovery + RTSP port sweep of the local network |
| `POST /connectors/onvif` | one ONVIF device's profiles and RTSP URLs |
| `GET /connectors/brands` | usual RTSP URLs per camera maker |
| `GET /connectors/presets` | public live webcams for one-click demos |
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, SecretStr

from rulecam.connectors.discovery import OnvifDevice, ScanResult, onvif_streams, scan
from rulecam.connectors.errors import DiscoveryError, SourceError, UnknownCameraError
from rulecam.connectors.manager import AddCamera, CameraView, ConnectorManager
from rulecam.connectors.sources import BRAND_TEMPLATES, LIVE_PRESETS, BrandTemplate, LivePreset
from rulecam.connectors.survey import CameraSurvey

MAX_CLIP_BYTES: Final = 500 * 1024 * 1024
CLIP_SUFFIXES: Final = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts"})
READ_BLOCK: Final = 1024 * 1024
CameraId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]


class ScanRequest(BaseModel):
    """Body of `POST /connectors/scan`."""

    timeout_s: float = Field(default=3.0, ge=0.5, le=10.0)
    sweep: bool = Field(default=True, description="Also try RTSP ports on the local /24.")


class OnvifRequest(BaseModel):
    """Body of `POST /connectors/onvif`."""

    target: str = Field(
        min_length=1, max_length=300, description="IP, host:port or device service URL."
    )
    username: str | None = Field(default=None, max_length=128)
    password: SecretStr | None = Field(default=None, max_length=256)


async def _save_clip(clip: UploadFile, folder: Path) -> Path:
    suffix = Path(clip.filename or "").suffix.lower()
    if suffix not in CLIP_SUFFIXES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "send a video file (MP4...)")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"clip-{uuid.uuid4().hex[:12]}{suffix}"
    size = 0
    with path.open("wb") as out:
        while block := await clip.read(READ_BLOCK):
            size += len(block)
            if size > MAX_CLIP_BYTES:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "clips up to 500 MB")
            out.write(block)
    return path


def connectors_router(manager: ConnectorManager, *, dependencies: Sequence[Any] = ()) -> APIRouter:
    """The `/connectors` routes (see the module docstring)."""

    @contextlib.asynccontextmanager
    async def lifespan(_: object) -> AsyncIterator[None]:
        # Saved cameras start with the app; every ffmpeg stops with it (uvicorn re-raises
        # SIGTERM after shutdown, so atexit would never run and ffmpeg would be orphaned).
        await asyncio.to_thread(manager.tick)
        try:
            yield
        finally:
            await asyncio.to_thread(manager.close)

    router = APIRouter(
        prefix="/connectors",
        tags=["connectors"],
        dependencies=list(dependencies),
        lifespan=lifespan,
    )
    _camera_routes(router, manager)
    _upload_route(router, manager)
    _look_routes(router, manager)
    _discovery_routes(router)
    return router


def _camera_routes(router: APIRouter, manager: ConnectorManager) -> None:
    @router.get("/cameras")
    def cameras() -> list[CameraView]:
        return manager.cameras()

    @router.post("/cameras", status_code=status.HTTP_201_CREATED)
    async def add(body: AddCamera) -> CameraView:
        try:
            return await asyncio.to_thread(manager.add, body)
        except SourceError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    @router.get("/cameras/{camera_id}")
    def camera(camera_id: CameraId) -> CameraView:
        try:
            return manager.camera(camera_id)
        except UnknownCameraError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.delete("/cameras/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove(camera_id: CameraId) -> Response:
        try:
            await asyncio.to_thread(manager.remove, camera_id)
        except UnknownCameraError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/cameras/{camera_id}/restart")
    async def restart(camera_id: CameraId) -> CameraView:
        try:
            return await asyncio.to_thread(manager.restart, camera_id)
        except UnknownCameraError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


def _upload_route(router: APIRouter, manager: ConnectorManager) -> None:
    @router.post("/cameras/upload", status_code=status.HTTP_201_CREATED)
    async def upload(
        clip: Annotated[UploadFile, File()],
        name: Annotated[str, Form(max_length=60)] = "",
    ) -> CameraView:
        path = await _save_clip(clip, manager.root / "uploads")
        return await asyncio.to_thread(manager.add_clip, path, name)


def _look_routes(router: APIRouter, manager: ConnectorManager) -> None:
    @router.get("/snapshot/{camera_id}.jpg")
    async def snapshot(camera_id: CameraId) -> FileResponse:
        path = await asyncio.to_thread(manager.snapshot, camera_id)
        if path is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no frame from that camera yet")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @router.post("/survey/{camera_id}")
    def start_survey(camera_id: CameraId) -> CameraSurvey:
        return manager.start_survey(camera_id)

    @router.get("/survey/{camera_id}")
    def survey(camera_id: CameraId) -> CameraSurvey:
        found = manager.survey(camera_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no survey of that camera yet")
        return found

    @router.get("/images/{name}")
    def image(name: str) -> FileResponse:
        path = manager.survey_image(name)
        if path is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such image")
        return FileResponse(path, media_type="image/jpeg")


def _discovery_routes(router: APIRouter) -> None:
    @router.post("/scan")
    async def scan_network(body: ScanRequest) -> ScanResult:
        return await asyncio.to_thread(scan, body.timeout_s, sweep=body.sweep)

    @router.post("/onvif")
    async def onvif(body: OnvifRequest) -> OnvifDevice:
        password = body.password.get_secret_value() if body.password else None
        try:
            return await asyncio.to_thread(onvif_streams, body.target, body.username, password)
        except DiscoveryError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    @router.get("/brands")
    def brands() -> list[BrandTemplate]:
        return list(BRAND_TEMPLATES)

    @router.get("/presets")
    def presets() -> list[LivePreset]:
        return list(LIVE_PRESETS)
