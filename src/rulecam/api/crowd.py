"""`/crowd`: crowd early warning at gates, stations and venues (`rulecam.crowd`).

- `POST /crowd/watch` starts the warden on a camera with the operator's own instruction, in any
  language ("warn me before gate 3 gets dangerous; tell people in Hindi and English to use gate
  4"); `DELETE /crowd/watch/{camera_id}` stops it.
- `GET /crowd/status` is everything the live page shows: each camera's newest reading, the last
  minutes as series, the warden's assessment and its last alert.
- `GET /crowd/alerts` lists alerts sent and held (with Glimmer's check).
- `GET /crowd/files/{name}` returns a heatmap or an announcement's WAV.
- `GET /crowd/live` is the live page (one HTML file, no build step), which polls the status,
  plays new announcements and asks questions through `POST /ask`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import timedelta
from importlib import resources
from pathlib import Path
from typing import Any, Final

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from rulecam.crowd.memory import CrowdMemory
from rulecam.crowd.scan import CrowdReading
from rulecam.crowd.warden import CrowdAlert, CrowdWarden, FloorPoints, Watch
from rulecam.perception.errors import PerceptionError

HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422
HTTP_UNAVAILABLE = 503
UNAVAILABLE: Final = "crowd watch needs RULECAM_PROTOTYPE_MODE=true and RULECAM_CROWD_EVERY_S > 0"
SERIES_S: Final = 600.0  # the live page's charts cover the last ten minutes
_FILE_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\.(jpg|wav)$")


class WatchRequest(BaseModel):
    """The operator's standing instruction for one camera."""

    camera_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    instruction: str = Field(min_length=1, max_length=2000)
    languages: list[str] = Field(default_factory=list, max_length=8)
    role: str = Field(default="security", min_length=1, max_length=32)
    floor: FloorPoints | None = Field(
        default=None,
        description="Floor points the operator marked (pixels and metres), for cameras the "
        "crowd cannot calibrate itself, such as an overhead view.",
    )


class CameraStatus(BaseModel):
    """One camera on the live page."""

    camera_id: str
    name: str
    watched: Watch | None = None
    latest: CrowdReading | None = None
    series: dict[str, list[Any]] = Field(default_factory=dict)
    assessment: dict[str, Any] | None = None
    assessed_at: str | None = None
    assess_seconds: float | None = None
    last_alert: CrowdAlert | None = None


class CrowdStatus(BaseModel):
    """`GET /crowd/status`."""

    cameras: list[CameraStatus]
    alerts: list[CrowdAlert]


def series(readings: Sequence[CrowdReading]) -> dict[str, list[Any]]:
    """The whole view's numbers over time, as columns for the charts."""
    out: dict[str, list[Any]] = {
        "t": [],
        "people": [],
        "density": [],
        "peak": [],
        "speed": [],
        "pressure": [],
        "coherence": [],
    }
    for reading in readings:
        view = reading.zones[0]
        out["t"].append(reading.at.isoformat())
        out["people"].append(view.people)
        out["density"].append(view.density_ppm2)
        out["peak"].append(reading.peak_density_ppm2)
        out["speed"].append(view.speed_mps)
        out["pressure"].append(view.pressure)
        out["coherence"].append(view.coherence)
    return out


def crowd_status(memory: CrowdMemory, warden: CrowdWarden, names: dict[str, str]) -> CrowdStatus:
    """Everything the live page shows, for every camera with readings or a watch."""
    states = {s.watch.camera_id: s for s in warden.states()}
    cameras = []
    for camera_id in sorted(set(memory.cameras()) | set(states)):
        latest = memory.latest(camera_id)
        since = latest.at - timedelta(seconds=SERIES_S) if latest is not None else None
        state = states.get(camera_id)
        camera = CameraStatus(
            camera_id=camera_id,
            name=names.get(camera_id, camera_id),
            latest=latest,
            series=series(memory.readings(camera_id, since)),
        )
        if state is not None:
            camera = camera.model_copy(
                update={
                    "watched": state.watch,
                    "assessment": (
                        state.assessment.model_dump(mode="json") if state.assessment else None
                    ),
                    "assessed_at": state.assessed_at.isoformat() if state.assessed_at else None,
                    "assess_seconds": state.seconds,
                    "last_alert": state.last_alert,
                }
            )
        cameras.append(camera)
    return CrowdStatus(cameras=cameras, alerts=warden.alerts(20))


def crowd_file(memory: CrowdMemory, warden: CrowdWarden, name: str) -> Path:
    """The heatmap or announcement called `name`.

    Raises:
        HTTPException: 404 for any other name, or a file that is not there.
    """
    if not _FILE_NAME.match(name):
        raise HTTPException(HTTP_NOT_FOUND, "no such file")
    path = (memory.heatmaps if name.endswith(".jpg") else warden.audio_dir) / name
    if not path.is_file():
        raise HTTPException(HTTP_NOT_FOUND, "no such file")
    return path


def crowd_router(
    memory: CrowdMemory | None,
    warden: CrowdWarden | None,
    *,
    camera_names: dict[str, str] | None = None,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    """The `/crowd` routes; 503 when crowd watch is off.

    Args:
        memory: The crowd readings, or `None` when off.
        warden: The early-warning agent, or `None` when off.
        camera_names: Camera id -> the owner's name for it.
        dependencies: Route dependencies (the owner check).

    Returns:
        The router.
    """
    router = APIRouter(dependencies=list(dependencies))
    names = dict(camera_names or {})

    def ready() -> tuple[CrowdMemory, CrowdWarden]:
        if memory is None or warden is None:
            raise HTTPException(HTTP_UNAVAILABLE, UNAVAILABLE)
        return memory, warden

    @router.post("/crowd/watch", response_model=Watch, tags=["crowd"])
    def watch(body: WatchRequest) -> Watch:
        """Start (or replace) the warden's instruction for a camera."""
        _, chosen = ready()
        try:
            return chosen.watch(
                body.camera_id, body.instruction, body.languages, body.role, body.floor
            )
        except PerceptionError as exc:
            raise HTTPException(HTTP_UNPROCESSABLE, f"floor points: {exc}") from exc

    @router.delete("/crowd/watch/{camera_id}", tags=["crowd"])
    def unwatch(camera_id: str) -> dict[str, bool]:
        """Stop watching a camera."""
        _, chosen = ready()
        return {"stopped": chosen.unwatch(camera_id)}

    @router.get("/crowd/status", response_model=CrowdStatus, tags=["crowd"])
    def status() -> CrowdStatus:
        """Each camera's newest reading, recent series, assessment and last alert."""
        found, chosen = ready()
        return crowd_status(found, chosen, names)

    @router.get("/crowd/alerts", response_model=list[CrowdAlert], tags=["crowd"])
    def alerts(limit: int = 50) -> list[CrowdAlert]:
        """Alerts sent and held, newest first."""
        _, chosen = ready()
        return chosen.alerts(max(1, min(limit, 500)))

    @router.get("/crowd/files/{name}", response_class=FileResponse, tags=["crowd"])
    def file(name: str) -> FileResponse:
        """A heatmap (`.jpg`) or an announcement (`.wav`)."""
        found, chosen = ready()
        path = crowd_file(found, chosen, name)
        media = "image/jpeg" if name.endswith(".jpg") else "audio/wav"
        return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})

    @router.get("/crowd/live", response_class=HTMLResponse, tags=["crowd"])
    def live() -> HTMLResponse:
        """The live page."""
        page = resources.files("rulecam.crowd").joinpath("live.html").read_text("utf-8")
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    return router
