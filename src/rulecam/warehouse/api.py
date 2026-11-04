"""`/warehouse` (owner-only): the forklift use case's story, near misses, heatmap and talks.

- `GET /warehouse/story?plan_id=&day=`: one plan's connected steps (`WarehouseStory`). Without
  `plan_id`, the newest approved plan with a distance rule, else the newest plan.
- `GET /warehouse/near-misses?day=&camera_id=&plan_id=`: the day's near misses, newest first.
- `GET /warehouse/heatmap?camera_id=&day=&plan_id=`: where they happened on one camera.
- `GET /warehouse/heatmap/{camera_id}/frame?day=`: a recorded frame of that camera (JPEG).
- `GET|POST /warehouse/incidents/{event_id}/warning`: the spoken warning in `lang` (POST makes
  it: Spark writes, MMS-TTS speaks, Voice Transcribe checks).
- `GET|POST /warehouse/incidents/{event_id}/toolbox-talk`: the toolbox talk in `lang` (POST
  writes it: Spark from the records and frames, Muse Image draws the safe behaviour).
- `GET|POST /warehouse/shift-report`: the shift safety report of a day (POST writes it).
- `GET /warehouse/files/{name}`: a WAV or PNG made above.

GET of a talk, warning or report returns the saved one (404 when none); POST calls the models
(503 when Spark is not configured, 502 when the call fails).
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from rulecam.api.plans import PlanRegistry
from rulecam.ask.vision import FrameGrabber
from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError, ModelClientError
from rulecam.clients.meta_hosted.image import MuseImageClient
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.clients.meta_hosted.voice import VoiceTranscribeClient
from rulecam.config import Settings
from rulecam.contracts import TTS, Clock, EventRecord, SitePlan, Track, Transcriber
from rulecam.errors import RulecamError
from rulecam.evidence.chain import ChainReport, read_chain, verify_chain
from rulecam.evidence.merkle import chain_root
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache
from rulecam.rule_ci.lookalikes import load_regressions
from rulecam.runtime.factory import Perception
from rulecam.runtime.loop import RuntimeLoop
from rulecam.warehouse.models import (
    Heatmap,
    NearMiss,
    ShiftReport,
    ShiftRequest,
    SpokenWarning,
    TalkRequest,
    ToolboxTalk,
    WarehouseStory,
)
from rulecam.warehouse.near_misses import heatmap, measures_distance, near_misses
from rulecam.warehouse.story import build_story, followed_incident
from rulecam.warehouse.talks import (
    Attacher,
    ImageMaker,
    Incident,
    ShiftReporter,
    Store,
    StructuredModel,
    ToolboxTalkWriter,
    WarningMaker,
    incident_from_records,
    shift_counts,
)

_log = get_logger(__name__)

EventId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
CameraId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")]
Lang = Annotated[str, Query(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")]


class PlanSource:
    """What the warehouse needs from the plan registry and the runtime."""

    def __init__(
        self,
        plans: Callable[[], Sequence[tuple[SitePlan, bool]]],
        active_rules: Callable[[], frozenset[str]],
    ) -> None:
        """Create the source.

        Args:
            plans: Every plan and whether it is approved, oldest first.
            active_rules: Rule ids the runtime enforces now.
        """
        self.plans = plans
        self.active_rules = active_rules

    def pick(self, plan_id: str | None) -> tuple[SitePlan, bool] | None:
        """The plan asked for, else the newest approved one with a distance rule, else newest."""
        every = list(self.plans())
        if plan_id is not None:
            return next(((p, ok) for p, ok in every if p.plan_id == plan_id), None)
        for plan, ok in reversed(every):
            if ok and any(_has_distance(rule.predicate) for rule in plan.rules):
                return plan, ok
        return every[-1] if every else None


def _has_distance(predicate: object) -> bool:
    dumped = predicate.model_dump(mode="json") if hasattr(predicate, "model_dump") else {}
    return measures_distance({"predicate": dumped})


TrackWindow = Callable[[str, datetime, datetime], Sequence[Track]]
FrameAt = Callable[[str, datetime], Path | None]


@dataclass
class WarehouseService:
    """Reads the chain, plans and tracks, and runs the writers."""

    chain_dir: Path
    regressions_dir: Path
    stills_root: Path | None
    plans: PlanSource
    store: Store
    tracks: TrackWindow | None = None
    frame_at: FrameAt | None = None
    warnings: WarningMaker | None = None
    talks: ToolboxTalkWriter | None = None
    shifts: ShiftReporter | None = None

    def days(self) -> list[date]:
        """Days with a chain, oldest first."""
        out = []
        for path in sorted(self.chain_dir.glob("*.jsonl")) if self.chain_dir.is_dir() else []:
            try:
                out.append(date.fromisoformat(path.stem))
            except ValueError:
                continue
        return out

    def day(self, asked: date | None) -> date:
        """The asked day, else the newest day with a chain, else today (UTC)."""
        days = self.days()
        return asked or (days[-1] if days else datetime.now(UTC).date())

    def records(self, day: date) -> list[EventRecord]:
        """The day's chain records (none when the day has no chain)."""
        path = self.chain_dir / f"{day.isoformat()}.jsonl"
        return read_chain(path) if path.is_file() else []

    def chain_report(self, day: date) -> ChainReport | None:
        """The day's chain verification, `None` when the day has no chain."""
        path = self.chain_dir / f"{day.isoformat()}.jsonl"
        return verify_chain(path) if path.is_file() else None

    def near(
        self, day: date, *, plan_id: str | None = None, camera_id: str | None = None
    ) -> list[NearMiss]:
        """The day's near misses."""
        return near_misses(
            self.records(day), plan_id=plan_id, camera_id=camera_id, tracks=self.tracks
        )

    def story(self, plan_id: str | None, day: date | None) -> WarehouseStory:
        """One plan's story for a day."""
        chosen_day = self.day(day)
        picked = self.plans.pick(plan_id)
        if picked is None:
            if plan_id is not None:
                raise HTTPException(404, f"no plan {plan_id!r}")
            return build_story(None, approved=False, active=False, near=[], chain=None)
        plan, approved = picked
        near = self.near(chosen_day, plan_id=plan.plan_id)
        active_ids = self.plans.active_rules()
        active = approved and bool(plan.rules) and all(r.id in active_ids for r in plan.rules)
        followed = followed_incident(near)
        talk = warning = None
        if followed is not None:
            talk = self._latest(ToolboxTalk, "talks", followed.event_id)
            warning = self._latest(SpokenWarning, "warnings", followed.event_id)
        regressions = sum(
            len(load_regressions(rule.id, self.regressions_dir)) for rule in plan.rules
        )
        return build_story(
            plan,
            approved=approved,
            active=active,
            near=near,
            chain=self.chain_report(chosen_day),
            talk=talk,
            warning=warning,
            regressions=regressions,
        )

    def _latest[M: ToolboxTalk | SpokenWarning](
        self, model: type[M], kind: str, event_id: str
    ) -> M | None:
        folder = self.store.root / kind
        found = sorted(folder.glob(f"{event_id}.*.json")) if folder.is_dir() else []
        loaded = [self.store.load(kind, p.stem, model) for p in found]
        kept = [item for item in loaded if item is not None]
        return max(kept, key=lambda item: item.created_at) if kept else None

    def heat(self, camera_id: str | None, day: date | None, plan_id: str | None) -> Heatmap:
        """The heatmap of one camera (the newest near miss's camera by default)."""
        chosen_day = self.day(day)
        near = self.near(chosen_day, plan_id=plan_id)
        camera = camera_id or (near[0].camera_id if near else None)
        if camera is None:
            raise HTTPException(404, "no near miss on that day")
        frame = self.frame(camera, chosen_day)
        size = _image_size(frame) if frame is not None else None
        return heatmap(
            near,
            camera_id=camera,
            day=chosen_day,
            width=size[0] if size else None,
            height=size[1] if size else None,
            background=(
                f"/warehouse/heatmap/{camera}/frame?day={chosen_day.isoformat()}"
                if frame is not None
                else None
            ),
        )

    def frame(self, camera_id: str, day: date) -> Path | None:
        """A recorded frame of the camera at its newest near miss (cached)."""
        cached = self.store.files / f"frame-{camera_id}-{day.isoformat()}.jpg"
        if cached.is_file():
            return cached
        near = self.near(day, camera_id=camera_id)
        if not near or self.frame_at is None:
            return None
        try:
            found = self.frame_at(camera_id, near[0].ts)
        except (RulecamError, OSError) as exc:
            _log.warning("warehouse_frame_failed", camera_id=camera_id, error=type(exc).__name__)
            return None
        if found is None:
            return None
        self.store.put_file(cached.name, found)
        return cached

    def incident(self, event_id: str) -> Incident:
        """One incident's records, from whichever day holds it."""
        for day in reversed(self.days()):
            found = incident_from_records(self.records(day), event_id, self.stills_root)
            if found is not None:
                return found
        raise HTTPException(404, "no incident with this event id")


def _image_size(path: Path) -> tuple[int, int] | None:
    import cv2  # noqa: PLC0415 - heavy import only when a frame is sized

    pixels = cv2.imread(str(path))
    return (int(pixels.shape[1]), int(pixels.shape[0])) if pixels is not None else None


def _model_call[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except ClientConfigError as exc:
        raise HTTPException(503, f"model not configured: {exc}") from exc
    except (ModelClientError, RulecamError) as exc:
        _log.warning("warehouse_model_failed", error=str(exc))
        raise HTTPException(502, f"model call failed: {type(exc).__name__}") from exc


def warehouse_router(  # noqa: C901 - one closure per route
    service: WarehouseService, *, dependencies: Sequence[Any] = ()
) -> APIRouter:
    """The `/warehouse` routes over `service`."""
    router = APIRouter(prefix="/warehouse", tags=["warehouse"], dependencies=list(dependencies))

    @router.get("/story")
    def story(plan_id: str | None = None, day: date | None = None) -> WarehouseStory:
        return service.story(plan_id, day)

    @router.get("/near-misses")
    def near(
        day: date | None = None, camera_id: str | None = None, plan_id: str | None = None
    ) -> list[NearMiss]:
        return service.near(service.day(day), plan_id=plan_id, camera_id=camera_id)

    @router.get("/heatmap")
    def heat(
        camera_id: str | None = None, day: date | None = None, plan_id: str | None = None
    ) -> Heatmap:
        return service.heat(camera_id, day, plan_id)

    @router.get("/heatmap/{camera_id}/frame", response_class=FileResponse)
    def frame(camera_id: CameraId, day: date | None = None) -> FileResponse:
        found = service.frame(camera_id, service.day(day))
        if found is None:
            raise HTTPException(404, "no recorded frame for that camera and day")
        return FileResponse(found, media_type="image/jpeg")

    @router.get("/incidents/{event_id}/warning")
    def get_warning(event_id: EventId, lang: Lang = "es") -> SpokenWarning:
        found = service.store.load("warnings", f"{event_id}.{lang}", SpokenWarning)
        if found is None:
            raise HTTPException(404, "no warning made yet")
        return found

    @router.post("/incidents/{event_id}/warning")
    def make_warning(event_id: EventId, body: TalkRequest) -> SpokenWarning:
        maker = service.warnings
        if maker is None:
            raise HTTPException(503, "Muse Spark is not configured")
        incident = service.incident(event_id)
        return _model_call(lambda: maker.make(incident, body.lang))

    @router.get("/incidents/{event_id}/toolbox-talk")
    def get_talk(event_id: EventId, lang: Lang = "es") -> ToolboxTalk:
        found = service.store.load("talks", f"{event_id}.{lang}", ToolboxTalk)
        if found is None:
            raise HTTPException(404, "no toolbox talk written yet")
        return found

    @router.post("/incidents/{event_id}/toolbox-talk")
    def write_talk(event_id: EventId, body: TalkRequest) -> ToolboxTalk:
        writer = service.talks
        if writer is None:
            raise HTTPException(503, "Muse Spark is not configured")
        incident = service.incident(event_id)
        return _model_call(lambda: writer.write(incident, body.lang, illustrate=body.illustrate))

    @router.get("/shift-report")
    def get_shift(day: date | None = None, lang: Lang = "en") -> ShiftReport:
        found = service.store.load("shifts", f"{service.day(day).isoformat()}.{lang}", ShiftReport)
        if found is None:
            raise HTTPException(404, "no shift report written yet")
        return found

    @router.post("/shift-report")
    def write_shift(body: ShiftRequest) -> ShiftReport:
        reporter = service.shifts
        if reporter is None:
            raise HTTPException(503, "Muse Spark is not configured")
        chosen = service.day(body.day)
        records = service.records(chosen)
        near = service.near(chosen)
        path = service.chain_dir / f"{chosen.isoformat()}.jsonl"
        root = chain_root(path)[0] if path.is_file() else None
        counts = shift_counts(records, chosen, near)
        return _model_call(lambda: reporter.write(counts, near, lang=body.lang, chain_root=root))

    @router.get("/files/{name}", response_class=FileResponse)
    def file(name: Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_.-]{1,160}$")]) -> FileResponse:
        found = service.store.file(name)
        if found is None:
            raise HTTPException(404, "no such file")
        media = {".wav": "audio/wav", ".png": "image/png", ".jpg": "image/jpeg"}
        return FileResponse(found, media_type=media.get(found.suffix, "application/octet-stream"))

    return router


def speech_engines(bus: Bus) -> list[tuple[str, TTS]]:
    """Meta MMS-TTS first (when `transformers` is installed), then the system voice."""
    from rulecam.voice_out.system_tts import SystemTts  # noqa: PLC0415 - optional engines
    from rulecam.voice_out.tts import MmsTts  # noqa: PLC0415

    engines: list[tuple[str, TTS]] = []
    if importlib.util.find_spec("transformers") is not None:
        engines.append(("Meta MMS-TTS", MmsTts(bus)))
    engines.append(("system voice (not a Meta model)", SystemTts(bus)))
    return engines


@dataclass(frozen=True)
class Writers:
    """The model-backed writers, any of which may be missing."""

    warnings: WarningMaker | None
    talks: ToolboxTalkWriter | None
    shifts: ShiftReporter | None


def build_writers(
    settings: Settings, *, cache: ReplayCache, clock: Clock, bus: Bus, store: Store
) -> Writers:
    """Spark, Muse Image and Voice Transcribe writers from settings (missing ones are `None`)."""
    try:
        spark = SparkClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    except ClientConfigError as exc:
        _log.warning("warehouse_without_spark", error=str(exc))
        return Writers(None, None, None)
    image: ImageMaker | None
    try:
        image = MuseImageClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    except ClientConfigError:
        image = None
    transcriber: Transcriber | None
    try:
        transcriber = VoiceTranscribeClient.from_settings(
            settings, clock=clock, bus=bus, cache=cache
        )
    except ClientConfigError:
        transcriber = None
    model: StructuredModel = spark
    attach: Attacher = spark
    return Writers(
        warnings=WarningMaker(
            model, store=store, clock=clock, tts=speech_engines(bus), transcriber=transcriber
        ),
        talks=ToolboxTalkWriter(model, store=store, clock=clock, attach=attach, image=image),
        shifts=ShiftReporter(model, store=store, clock=clock),
    )


def build_warehouse(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    chain_dir: Path,
    plans: PlanRegistry,
    runtime: RuntimeLoop | None,
    perception: Perception,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    """The `/warehouse` router, wired to the app's chain, plans, runtime and perception.

    Args:
        settings: Application settings.
        cache: The replay cache every model call goes through.
        clock: Time source.
        bus: For `relay.model_used`.
        chain_dir: The site's chain folder (`<chain root>/<site>`).
        plans: The plan registry.
        runtime: The runtime loop (for the rules it enforces now).
        perception: The track store and recorded chunks.
        dependencies: Route dependencies (owner login).

    Returns:
        The router.
    """
    data = settings.rulecam_data_dir
    store = Store(data / "warehouse")
    writers = build_writers(settings, cache=cache, clock=clock, bus=bus, store=store)
    grabber = FrameGrabber(perception.chunks, data / "warehouse" / "frames")

    def every_plan() -> list[tuple[SitePlan, bool]]:
        return [plans.get(summary.plan_id) for summary in plans.summaries()]

    def active_rules() -> frozenset[str]:
        plan = runtime.plan if runtime is not None else None
        return frozenset(rule.id for rule in plan.rules) if plan is not None else frozenset()

    def frame_at(camera_id: str, at: datetime) -> Path | None:
        found = grabber.frames(camera_id, [at])
        return found[0].path if found else None

    service = WarehouseService(
        chain_dir=chain_dir,
        regressions_dir=settings.regressions_dir,
        stills_root=data / "strips",
        plans=PlanSource(every_plan, active_rules),
        store=store,
        tracks=perception.store.window,
        frame_at=frame_at,
        warnings=writers.warnings,
        talks=writers.talks,
        shifts=writers.shifts,
    )
    return warehouse_router(service, dependencies=dependencies)
