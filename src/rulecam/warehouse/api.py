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
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from rulecam.api.plans import PlanRegistry
from rulecam.ask.vision import FrameGrabber
from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError, ModelClientError
from rulecam.clients.meta_hosted.image import MuseImageClient
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.clients.meta_hosted.voice import VoiceTranscribeClient
from rulecam.config import Settings
from rulecam.contracts import (
    TTS,
    Clock,
    EventRecord,
    RequestItem,
    SitePlan,
    Track,
    Transcriber,
)
from rulecam.errors import RulecamError
from rulecam.evidence.chain import ChainReport, EventChain, read_chain, verify_chain
from rulecam.evidence.merkle import chain_root
from rulecam.log import get_logger
from rulecam.planner.errors import PipelineError
from rulecam.planner.pipeline import PlanStore
from rulecam.replay.cache import ReplayCache
from rulecam.rule_ci.lookalikes import load_regressions
from rulecam.runtime.factory import Perception
from rulecam.runtime.loop import RuntimeLoop
from rulecam.warehouse.duty import ChainWriter, DutyWriter, GroundedModel
from rulecam.warehouse.models import (
    DutyDone,
    HeardReport,
    Heatmap,
    NearMiss,
    RefresherDuty,
    ShiftReport,
    ShiftRequest,
    SpokenWarning,
    TalkRequest,
    ToolboxTalk,
    VoiceNoteRequest,
    VoiceReport,
    WarehouseStory,
)
from rulecam.warehouse.near_misses import heatmap, measures_distance, near_misses
from rulecam.warehouse.planning import Planning, planning_timeline
from rulecam.warehouse.report import (
    MAX_AUDIO_BYTES,
    NearMissReporter,
    NearMissWindow,
    ReportError,
)
from rulecam.warehouse.story import build_story, followed_incident, planning_story
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
from rulecam.warehouse.voice_note import (
    VoiceChannel,
    VoiceNote,
    VoiceNoteError,
    VoiceNoteSender,
)
from rulecam.worker.disputes import DailyLimiter

_log = get_logger(__name__)

PUBLIC_REPORTS_PER_CLIENT = 5
PUBLIC_REPORTS_PER_DAY = 200
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


class PlannerSubmit(Protocol):
    """Starts the Site Planner on a request (`PlannerService.submit`)."""

    def submit(self, items: Sequence[RequestItem]) -> str:
        """Queue a planning run and return its plan id."""
        ...


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
    duties: DutyWriter | None = None
    reports: NearMissReporter | None = None
    planner: PlannerSubmit | None = None
    voice_notes: VoiceNoteSender | None = None
    plan_store: PlanStore | None = None

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

    def near_between(self, t0: datetime, t1: datetime) -> list[NearMiss]:
        """Near misses whose episode ended between two instants (any camera, any plan)."""
        days = {t0.date(), t1.date()}
        rows = [n for day in sorted(days) for n in self.near(day)]
        return [n for n in rows if t0 <= n.ts <= t1]

    def propose(self, report_id: str) -> VoiceReport:
        """Send a voice report's proposed rule to the Site Planner (once)."""
        if self.reports is None or self.planner is None:
            raise HTTPException(503, "the Site Planner is not running")
        found = self.reports.get(report_id)
        if found is None:
            raise HTTPException(404, "no such report")
        if found.plan_id is not None:
            return found
        item = RequestItem(kind="text", text=found.proposed_rule, lang=found.lang or None)
        updated = self.reports.proposed(report_id, self.planner.submit([item]))
        return updated or found

    def story(self, plan_id: str | None, day: date | None) -> WarehouseStory:
        """One plan's story for a day."""
        chosen_day = self.day(day)
        picked = self.plans.pick(plan_id)
        if picked is None:
            if plan_id is not None:
                return self._planning_story(plan_id)
            return build_story(None, approved=False, active=False, near=[], chain=None)
        plan, approved = picked
        near = self.near(chosen_day, plan_id=plan.plan_id)
        active_ids = self.plans.active_rules()
        active = approved and bool(plan.rules) and all(r.id in active_ids for r in plan.rules)
        followed = followed_incident(near)
        talk = warning = duty = None
        if followed is not None:
            talk = self._latest(ToolboxTalk, "talks", followed.event_id)
            warning = self._latest(SpokenWarning, "warnings", followed.event_id)
            duty = self.duties.get(followed.event_id) if self.duties is not None else None
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
            duty=duty,
            regressions=regressions,
            planning=self.planning(plan.plan_id),
        )

    def planning(self, plan_id: str) -> Planning | None:
        """The plan's step timings from its saved run (`None` without a plan store or run)."""
        if self.plan_store is None:
            return None
        return planning_timeline(self.plan_store, plan_id, datetime.now(UTC))

    def _planning_story(self, plan_id: str) -> WarehouseStory:
        """The story of a plan still being planned, or 404 when there is no such run."""
        timeline = self.planning(plan_id)
        if timeline is None or self.plan_store is None:
            raise HTTPException(404, f"no plan {plan_id!r}")
        try:
            items = self.plan_store.load_input(plan_id).request
        except PipelineError:
            items = ()
        first = next((i for i in items if i.text or i.extracted), None)
        text = (first.text or " ".join(first.extracted)) if first is not None else ""
        return planning_story(plan_id, text, first.lang if first else None, timeline)

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
    except (ReportError, VoiceNoteError):
        raise
    except (ModelClientError, RulecamError) as exc:
        _log.warning("warehouse_model_failed", error=str(exc))
        raise HTTPException(502, f"model call failed: {type(exc).__name__}") from exc


def warehouse_router(service: WarehouseService, *, dependencies: Sequence[Any] = ()) -> APIRouter:
    """The `/warehouse` routes over `service`."""
    router = APIRouter(prefix="/warehouse", tags=["warehouse"], dependencies=list(dependencies))
    _read_routes(router, service)
    _talk_routes(router, service)
    _duty_routes(router, service)
    _report_routes(router, service)
    _voice_report_routes(router, service)
    _voice_note_routes(router, service)
    return router


def _read_routes(router: APIRouter, service: WarehouseService) -> None:
    """Story, near misses and heatmap."""

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


def _talk_routes(router: APIRouter, service: WarehouseService) -> None:
    """Spoken warning and toolbox talk of one incident."""

    @router.get("/incidents/{event_id}/warning")
    def get_warning(
        event_id: EventId, lang: Lang = "es", also: Lang | None = None
    ) -> SpokenWarning:
        key = f"{event_id}.{lang}" + (f"+{also}" if also and also != lang else "")
        found = service.store.load("warnings", key, SpokenWarning)
        if found is None:
            raise HTTPException(404, "no warning made yet")
        return found

    @router.post("/incidents/{event_id}/warning")
    def make_warning(event_id: EventId, body: TalkRequest) -> SpokenWarning:
        maker = service.warnings
        if maker is None:
            raise HTTPException(503, "Muse Spark is not configured")
        incident = service.incident(event_id)
        return _model_call(lambda: maker.make(incident, body.lang, also=body.also))

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


def _duty_routes(router: APIRouter, service: WarehouseService) -> None:
    """The refresher-training duty of one near miss."""

    @router.get("/duties")
    def duties() -> list[RefresherDuty]:
        return service.duties.every() if service.duties is not None else []

    @router.get("/incidents/{event_id}/duty")
    def get_duty(event_id: EventId) -> RefresherDuty:
        found = service.duties.get(event_id) if service.duties is not None else None
        if found is None:
            raise HTTPException(404, "no training duty raised for this near miss yet")
        return found

    @router.post("/incidents/{event_id}/duty")
    def raise_duty(event_id: EventId) -> RefresherDuty:
        writer = service.duties
        if writer is None:
            raise HTTPException(503, "Muse Spark is not configured")
        incident = service.incident(event_id)
        return _model_call(lambda: writer.raise_duty(incident))

    @router.post("/incidents/{event_id}/duty/done")
    def duty_done(event_id: EventId, body: DutyDone) -> RefresherDuty:
        closed = service.duties.done(event_id, body.role) if service.duties is not None else None
        if closed is None:
            raise HTTPException(404, "no training duty raised for this near miss yet")
        return closed


def _report_routes(router: APIRouter, service: WarehouseService) -> None:
    """Shift report and the files the writers made."""

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


def _voice_note_routes(router: APIRouter, service: WarehouseService) -> None:
    """The spoken warning sent to a role's phone as a WhatsApp voice note."""

    @router.post("/incidents/{event_id}/voice-note")
    def voice_note(event_id: EventId, body: VoiceNoteRequest) -> VoiceNote:
        sender = service.voice_notes
        if sender is None:
            raise HTTPException(503, "voice notes are not set up")
        key = f"{event_id}.{body.lang}" + (f"+{body.also}" if body.also else "")
        warning = service.store.load("warnings", key, SpokenWarning)
        if warning is None:
            raise HTTPException(404, "make the spoken warning first")
        try:
            return _model_call(lambda: sender.send(warning, body.role))
        except VoiceNoteError as exc:
            raise HTTPException(409, str(exc)) from exc


def _voice_report_routes(router: APIRouter, service: WarehouseService) -> None:
    """A worker's spoken near-miss report, and the rule it proposes."""

    @router.post("/reports/voice")
    async def report_voice(request: Request, lang: Lang | None = None) -> VoiceReport:
        reporter = service.reports
        if reporter is None:
            raise HTTPException(503, "Muse Spark or Voice Transcribe is not configured")
        audio = await request.body()
        if not audio:
            raise HTTPException(400, "audio body required")
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "audio exceeds limit")
        try:
            return await run_in_threadpool(_model_call, lambda: reporter.report(audio, lang=lang))
        except ReportError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/reports")
    def reports() -> list[VoiceReport]:
        return service.reports.every() if service.reports is not None else []

    @router.post("/reports/{report_id}/propose")
    def propose(
        report_id: Annotated[str, PathParam(pattern=r"^nr-[0-9a-f]{12}$")],
    ) -> VoiceReport:
        return service.propose(report_id)


def warehouse_public_router(
    service: WarehouseService,
    *,
    client_address: Callable[[Request], str],
    limiter: DailyLimiter,
) -> APIRouter:
    """`POST /warehouse/public/reports/voice`: a worker reports a near miss from the QR page.

    No login (the worker page is public): each client address gets a small daily quota, the
    audio is never kept, and the answer says only what was heard and whether a camera measured
    it. The owner sees the full report under `GET /warehouse/reports`.
    """
    router = APIRouter(prefix="/warehouse/public", tags=["warehouse"])

    @router.post("/reports/voice")
    async def report_voice(request: Request, lang: Lang | None = None) -> HeardReport:
        reporter = service.reports
        if reporter is None:
            raise HTTPException(503, "voice reports are not set up")
        if not limiter.allow(client_address(request)):
            raise HTTPException(429, "too many reports from here today")
        audio = await request.body()
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(400, "send one recording, at most 10 MB")
        try:
            report = await run_in_threadpool(_model_call, lambda: reporter.report(audio, lang=lang))
        except ReportError as exc:
            raise HTTPException(422, str(exc)) from exc
        return HeardReport(
            heard=report.transcript,
            what_happened=report.what_happened,
            matched=report.matches_event_id is not None,
            measured_m=report.measured_m,
        )

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
    duties: DutyWriter | None = None
    reports: NearMissReporter | None = None


def build_writers(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    store: Store,
    chain: ChainWriter,
    near: NearMissWindow,
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
    search: GroundedModel = spark
    return Writers(
        warnings=WarningMaker(
            model, store=store, clock=clock, tts=speech_engines(bus), transcriber=transcriber
        ),
        talks=ToolboxTalkWriter(model, store=store, clock=clock, attach=attach, image=image),
        shifts=ShiftReporter(model, store=store, clock=clock),
        duties=DutyWriter(
            model, search=search, store=store, clock=clock, chain=chain, attach=attach
        ),
        reports=(
            NearMissReporter(model, transcriber, store=store, clock=clock, chain=chain, near=near)
            if transcriber is not None
            else None
        ),
    )


def build_warehouse(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    chain: EventChain,
    plans: PlanRegistry,
    runtime: RuntimeLoop | None,
    perception: Perception,
    planner: PlannerSubmit | None = None,
    whatsapp: VoiceChannel | None = None,
    plans_dir: Path = Path("plans"),
    client_address: Callable[[Request], str] | None = None,
    dependencies: Sequence[Any] = (),
) -> tuple[APIRouter, ...]:
    """The `/warehouse` router, wired to the app's chain, plans, runtime and perception.

    Args:
        settings: Application settings.
        cache: The replay cache every model call goes through.
        clock: Time source.
        bus: For `relay.model_used`.
        chain: The site's evidence chain (read by day; "training done" records go to it).
        plans: The plan registry.
        runtime: The runtime loop (for the rules it enforces now).
        perception: The track store and recorded chunks.
        planner: The Site Planner (a worker's proposed rule is planned and Rule CI-tested).
        whatsapp: WhatsApp, for spoken warnings sent as voice notes (when configured).
        plans_dir: The planner's store (step timings of each run).
        client_address: The client's address for the public route's daily quota; without it
            the public voice-report route is not served.
        dependencies: Route dependencies (owner login).

    Returns:
        The owner router, and the public voice-report router when `client_address` is given.
    """
    data = settings.rulecam_data_dir
    store = Store(data / "warehouse")

    def near_between(t0: datetime, t1: datetime) -> list[NearMiss]:
        return service.near_between(t0, t1)

    writers = build_writers(
        settings, cache=cache, clock=clock, bus=bus, store=store, chain=chain, near=near_between
    )
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
        chain_dir=chain.path_for(clock.now().date()).parent,
        regressions_dir=settings.regressions_dir,
        stills_root=data / "strips",
        plans=PlanSource(every_plan, active_rules),
        store=store,
        tracks=perception.store.window,
        frame_at=frame_at,
        warnings=writers.warnings,
        talks=writers.talks,
        shifts=writers.shifts,
        duties=writers.duties,
        reports=writers.reports,
        planner=planner,
        voice_notes=VoiceNoteSender(
            whatsapp,
            chain=chain,
            store=store,
            clock=clock,
            dry_run=settings.rulecam_actions_dry_run,
        ),
        plan_store=PlanStore(plans_dir),
    )
    owner = warehouse_router(service, dependencies=dependencies)
    if client_address is None:
        return (owner,)
    limiter = DailyLimiter(
        clock, per_client=PUBLIC_REPORTS_PER_CLIENT, per_day=PUBLIC_REPORTS_PER_DAY
    )
    public = warehouse_public_router(service, client_address=client_address, limiter=limiter)
    return owner, public
