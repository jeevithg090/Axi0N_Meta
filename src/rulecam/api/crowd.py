"""`/crowd`: crowd early warning at gates, stations and venues (`rulecam.crowd`).

JSON and Server-Sent Events for the UI's crowd page (`ui/src/app/usecases/crowd`); the contract
is written up in `docs/usecases/crowd.md`.

- `POST /crowd/setup` asks Muse Spark to read the operator's instruction (any language) against
  the camera's newest frame and propose the watch: named zones on the frame, what to count, the
  languages and roles. Nothing starts until the operator sends `POST /crowd/watch`.
- `POST /crowd/watch` starts the warden on a camera with the operator's own instruction
  ("warn me before gate 3 gets dangerous; tell people in Hindi and English to use gate 4");
  `DELETE /crowd/watch/{camera_id}` stops it.
- `GET|PUT /crowd/venue`: the venue layout (gates, routes, exits and links) Spark plans actions on.
- `GET /crowd/status` is everything the live page shows: each camera's newest reading (with its
  grid cells, flow and counterflow), the last minutes as series per zone, each zone's projection,
  the warden's assessment, its alerts and who acknowledged them. `GET /crowd/stream` sends the
  same object as an SSE `status` event whenever it changes.
- `GET /crowd/alerts` lists alerts sent and held (with Glimmer's check);
  `POST /crowd/alerts/{alert_id}/ack` is a steward's tap (*On my way*, *False alarm*, *Escalate*).
- `GET /crowd/files/{name}` returns a heatmap, a recorded frame or an announcement's WAV.
- `PUT|GET /crowd/truth/{camera_id}` registers ground truth for a camera (a scored clip);
  `GET /crowd/report` is the after-event report: timeline, peaks, latency, the evidence chain,
  the ground-truth score and, with `narrate=true`, Muse Spark's after-action summary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from rulecam.crowd.forecast import ZoneForecast, forecast
from rulecam.crowd.memory import CrowdMemory
from rulecam.crowd.report import EventReport, Truth, after_action, build_report
from rulecam.crowd.scan import CrowdReading
from rulecam.crowd.setup import SetupProposal, propose
from rulecam.crowd.venue import Venue, place_readings
from rulecam.crowd.warden import AckState, CrowdAlert, CrowdWarden, FloorPoints, Watch
from rulecam.crowd.zones import CrowdZone
from rulecam.errors import RulecamError
from rulecam.perception.errors import PerceptionError

HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422
HTTP_BAD_GATEWAY = 502
HTTP_UNAVAILABLE = 503
UNAVAILABLE: Final = "crowd watch needs RULECAM_PROTOTYPE_MODE=true and RULECAM_CROWD_EVERY_S > 0"
SERIES_S: Final = 600.0  # the live page's charts cover the last ten minutes
STREAM_EVERY_S: Final = 1.0  # the SSE stream looks for changes this often
KEEPALIVE_S: Final = 15.0
_FILE_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\.(jpg|wav)$")
_CAMERA: Final = r"^[A-Za-z0-9_.:-]+$"
_ROLE: Final = r"^[A-Za-z][A-Za-z0-9_-]*$"


class WatchRequest(BaseModel):
    """The operator's standing instruction for one camera."""

    camera_id: str = Field(min_length=1, max_length=64, pattern=_CAMERA)
    instruction: str = Field(min_length=1, max_length=2000)
    languages: list[str] = Field(default_factory=list, max_length=8)
    role: str = Field(default="security", min_length=1, max_length=32, pattern=_ROLE)
    floor: FloorPoints | None = Field(
        default=None,
        description="Floor points the operator marked (pixels and metres), for cameras the "
        "crowd cannot calibrate itself, such as an overhead view.",
    )
    zones: list[CrowdZone] = Field(
        default_factory=list,
        max_length=8,
        description="Named areas to measure besides the whole view (a gate approach, a stair).",
    )
    concept: str | None = Field(
        default=None,
        max_length=64,
        description="What SAM 3.1 counts here: 'head' for a camera looking down, 'person' "
        "(the default) for one looking across.",
    )
    grid: int | None = Field(default=None, ge=1, le=4, description="Tiles per side.")
    escalate_to: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Roles an alert goes on to, in order, when nobody acknowledges it.",
    )
    ack_within_s: float = Field(
        default=60.0, gt=0.0, le=3600.0, description="Seconds to wait for a tap per role."
    )


class SetupRequest(BaseModel):
    """`POST /crowd/setup`: the operator's instruction and the camera to read it against."""

    camera_id: str = Field(min_length=1, max_length=64, pattern=_CAMERA)
    instruction: str = Field(min_length=1, max_length=2000)


class AckRequest(BaseModel):
    """A steward's tap on a crowd alert."""

    role: str = Field(min_length=1, max_length=64, pattern=_ROLE)
    button: Literal["On my way", "False alarm", "Escalate"] = "On my way"


class CameraStatus(BaseModel):
    """One camera on the live page."""

    camera_id: str
    name: str
    watched: Watch | None = None
    latest: CrowdReading | None = None
    series: dict[str, list[Any]] = Field(default_factory=dict)
    zone_series: dict[str, dict[str, list[Any]]] = Field(default_factory=dict)
    forecast: list[ZoneForecast] = Field(default_factory=list)
    assessment: dict[str, Any] | None = None
    assessed_at: str | None = None
    assess_seconds: float | None = None
    last_alert: CrowdAlert | None = None


class CrowdStatus(BaseModel):
    """`GET /crowd/status` (and each SSE `status` event)."""

    cameras: list[CameraStatus]
    alerts: list[CrowdAlert]
    acks: dict[str, AckState] = Field(default_factory=dict)
    venue: Venue = Field(default_factory=Venue)
    places: list[dict[str, Any]] = Field(default_factory=list)


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


def zone_series(readings: Sequence[CrowdReading]) -> dict[str, dict[str, list[Any]]]:
    """Each zone's people, density, speed and counterflow over time, as columns."""
    out: dict[str, dict[str, list[Any]]] = {}
    for reading in readings:
        for zone in reading.zones:
            columns = out.setdefault(
                zone.zone, {"t": [], "people": [], "density": [], "speed": [], "counterflow": []}
            )
            columns["t"].append(reading.at.isoformat())
            columns["people"].append(zone.people)
            columns["density"].append(zone.density_ppm2)
            columns["speed"].append(zone.speed_mps)
            columns["counterflow"].append(zone.counterflow_share)
    return out


def crowd_status(memory: CrowdMemory, warden: CrowdWarden, names: dict[str, str]) -> CrowdStatus:
    """Everything the live page shows, for every camera with readings or a watch."""
    states = {s.watch.camera_id: s for s in warden.states()}
    cameras = []
    for camera_id in sorted(set(memory.cameras()) | set(states)):
        latest = memory.latest(camera_id)
        since = latest.at - timedelta(seconds=SERIES_S) if latest is not None else None
        recent = memory.readings(camera_id, since)
        state = states.get(camera_id)
        camera = CameraStatus(
            camera_id=camera_id,
            name=names.get(camera_id, camera_id),
            latest=latest,
            series=series(recent),
            zone_series=zone_series(recent),
            forecast=forecast(recent),
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
    alerts = warden.alerts(20)
    venue = warden.venue
    return CrowdStatus(
        cameras=cameras,
        alerts=alerts,
        acks={a.alert_id: warden.ack_state(a.alert_id) for a in alerts if a.sent},
        venue=venue,
        places=place_readings(venue, memory.latest),
    )


def crowd_file(memory: CrowdMemory, warden: CrowdWarden, name: str) -> Path:
    """The heatmap, recorded frame or announcement called `name`.

    Raises:
        HTTPException: 404 for any other name, or a file that is not there.
    """
    if not _FILE_NAME.match(name):
        raise HTTPException(HTTP_NOT_FOUND, "no such file")
    folders = (memory.heatmaps, warden.frames_dir) if name.endswith(".jpg") else (warden.audio_dir,)
    for folder in folders:
        path = folder / name
        if path.is_file():
            return path
    raise HTTPException(HTTP_NOT_FOUND, "no such file")


def crowd_report(
    memory: CrowdMemory,
    warden: CrowdWarden,
    names: dict[str, str],
    camera_id: str,
    *,
    since: datetime | None,
    until: datetime | None,
) -> EventReport:
    """The after-event report of one camera (no narrative)."""
    watch = next((s.watch for s in warden.states() if s.watch.camera_id == camera_id), None)
    alert_ids = {a.alert_id for a in warden.alerts(500) if a.camera_id == camera_id}
    notified = [
        {"alert_id": alert_id, **n.model_dump()}
        for alert_id in alert_ids
        for n in warden.ack_state(alert_id).notified
    ]
    return build_report(
        camera_id=camera_id,
        name=names.get(camera_id, camera_id),
        readings_path=memory.path,
        assessments_path=warden.assessments_path,
        alerts_path=warden.alerts_path,
        acks_path=warden.acks_path,
        chain=warden.chain,
        since=since,
        until=until,
        clip_start=warden.first_recorded(camera_id),
        instruction=watch.instruction if watch is not None else None,
        truth=warden.truth(camera_id),
        notified=notified,
    )


def crowd_router(  # noqa: C901, PLR0915 - one small handler per route
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

    @router.post("/crowd/setup", response_model=SetupProposal, tags=["crowd"])
    async def setup(body: SetupRequest) -> SetupProposal:
        """Muse Spark's proposed watch for a camera, from the instruction and the newest frame."""
        found, chosen = ready()
        latest = found.latest(body.camera_id)
        try:
            return await asyncio.to_thread(
                propose,
                chosen.spark,
                chosen.files,
                chosen.frames,
                body.camera_id,
                body.instruction,
                name=names.get(body.camera_id, body.camera_id),
                people=latest.people if latest is not None else None,
            )
        except ValueError as exc:
            raise HTTPException(HTTP_UNPROCESSABLE, str(exc)) from exc
        except RulecamError as exc:
            raise HTTPException(HTTP_BAD_GATEWAY, f"Muse Spark: {exc}") from exc

    @router.post("/crowd/watch", response_model=Watch, tags=["crowd"])
    def watch(body: WatchRequest) -> Watch:
        """Start (or replace) the warden's instruction for a camera."""
        _, chosen = ready()
        try:
            return chosen.watch(
                body.camera_id,
                body.instruction,
                body.languages,
                role=body.role,
                floor=body.floor,
                zones=body.zones,
                concept=body.concept,
                grid=body.grid,
                escalate_to=body.escalate_to,
                ack_within_s=body.ack_within_s,
            )
        except PerceptionError as exc:
            raise HTTPException(HTTP_UNPROCESSABLE, f"floor points: {exc}") from exc

    @router.delete("/crowd/watch/{camera_id}", tags=["crowd"])
    def unwatch(camera_id: str) -> dict[str, bool]:
        """Stop watching a camera."""
        _, chosen = ready()
        return {"stopped": chosen.unwatch(camera_id)}

    @router.get("/crowd/venue", response_model=Venue, tags=["crowd"])
    def venue() -> Venue:
        """The venue layout."""
        _, chosen = ready()
        return chosen.venue

    @router.put("/crowd/venue", response_model=Venue, tags=["crowd"])
    def set_venue(body: Venue) -> Venue:
        """Replace the venue layout Spark plans staff actions on."""
        _, chosen = ready()
        return chosen.set_venue(body)

    @router.get("/crowd/status", response_model=CrowdStatus, tags=["crowd"])
    def status() -> CrowdStatus:
        """Each camera's newest reading, series, projection, assessment, alerts and acks."""
        found, chosen = ready()
        return crowd_status(found, chosen, names)

    @router.get("/crowd/stream", tags=["crowd"])
    async def stream(request: Request, limit: int | None = None) -> StreamingResponse:
        """`GET /crowd/status` as Server-Sent Events: a `status` event on every change.

        `limit` closes the stream after that many events (for scripts and tests).
        """
        found, chosen = ready()

        async def events() -> AsyncIterator[str]:
            last, quiet, sent = "", 0.0, 0
            while limit is None or sent < limit:
                if await request.is_disconnected():
                    return
                status = await asyncio.to_thread(crowd_status, found, chosen, names)
                body = status.model_dump_json()
                digest = hashlib.sha256(body.encode()).hexdigest()
                if digest != last:
                    last, quiet, sent = digest, 0.0, sent + 1
                    yield f"event: status\ndata: {body}\n\n"
                    continue
                quiet += STREAM_EVERY_S
                if quiet >= KEEPALIVE_S:
                    quiet = 0.0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(STREAM_EVERY_S)

        # `no-transform`: a proxy that compresses (the UI's Next.js rewrite does) must not
        # buffer the stream.
        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
        )

    @router.get("/crowd/alerts", response_model=list[CrowdAlert], tags=["crowd"])
    def alerts(limit: int = 50) -> list[CrowdAlert]:
        """Alerts sent and held, newest first."""
        _, chosen = ready()
        return chosen.alerts(max(1, min(limit, 500)))

    @router.post("/crowd/alerts/{alert_id}/ack", response_model=AckState, tags=["crowd"])
    def ack(alert_id: str, body: AckRequest) -> AckState:
        """A steward's tap: acknowledge, false alarm, or escalate to the next role now."""
        _, chosen = ready()
        try:
            return chosen.ack(alert_id, body.role, body.button)
        except KeyError as exc:
            raise HTTPException(HTTP_NOT_FOUND, "no such alert") from exc

    @router.get("/crowd/files/{name}", response_class=FileResponse, tags=["crowd"])
    def file(name: str) -> FileResponse:
        """A heatmap or recorded frame (`.jpg`) or an announcement (`.wav`)."""
        found, chosen = ready()
        path = crowd_file(found, chosen, name)
        media = "image/jpeg" if name.endswith(".jpg") else "audio/wav"
        return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})

    @router.put("/crowd/truth/{camera_id}", tags=["crowd"])
    def set_truth(camera_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Register a camera's ground truth (true people per zone over the clip)."""
        _, chosen = ready()
        if not re.match(_CAMERA, camera_id) or len(camera_id) > 64:  # noqa: PLR2004
            raise HTTPException(HTTP_UNPROCESSABLE, "bad camera id")
        try:
            truth = Truth.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(HTTP_UNPROCESSABLE, json.loads(exc.json())) from exc
        chosen.set_truth(camera_id, truth)
        return {"camera_id": camera_id, "zones": sorted(truth.zones), "source": truth.source}

    @router.get("/crowd/truth/{camera_id}", response_model=Truth, tags=["crowd"])
    def get_truth(camera_id: str) -> Truth:
        """A camera's registered ground truth."""
        _, chosen = ready()
        found = chosen.truth(camera_id) if re.match(_CAMERA, camera_id) else None
        if found is None:
            raise HTTPException(HTTP_NOT_FOUND, "no ground truth for this camera")
        return found

    @router.get("/crowd/report", response_model=EventReport, tags=["crowd"])
    async def report(
        camera_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        narrate: bool = False,
    ) -> EventReport:
        """The after-event report; `narrate=true` adds Muse Spark's after-action summary."""
        found, chosen = ready()
        made = await asyncio.to_thread(
            lambda: crowd_report(found, chosen, names, camera_id, since=since, until=until)
        )
        if not narrate:
            return made
        try:
            summary = await asyncio.to_thread(after_action, chosen.spark, made)
        except RulecamError as exc:
            raise HTTPException(HTTP_BAD_GATEWAY, f"Muse Spark: {exc}") from exc
        return made.model_copy(update={"after_action": summary})

    return router
