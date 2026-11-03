"""The Rulecam API (task P3-08): one FastAPI app for the UI, phones and the verify page.

`create_app(services)` builds the app from injected services, so tests and the demo wire exactly
what they need. It mounts `/pair` (P1-09) and `/correction` (P1-10) when those services are set,
plus `/plans`, `/cameras`, `/metrics/perception`, the Prometheus `/metrics` (WS5) and the static
`/verify` page. Routers owned by
tasks still in flight (`/ack`, `/interlock`, `/advice`, `/surprise`) are passed in through
`AppServices.routers` as they land; `services_from_settings` passes the public `/worker` router
(P2-11) that way.

Approving a plan (`POST /plans/{id}/approve`) activates it in the runtime through
`AppServices.activator` (`rulecam.runtime.activate`); `services_from_settings` persists the active
plan under `<data>/runtime/active_plan.json` and restores it at start-up.

Anything that changes state (`PUT /mode`, `PUT /runtime/plan`, plan create/approve/answers,
`POST /correction`, pairing) is owner-only: the request comes from this machine (loopback host
and client), or carries a signed owner session from `POST /auth/login` plus, for writes, an
allowed `Origin` and the CSRF header (`rulecam.api.auth`). Reads that expose site content (the
read-only `/ws` mirror, `GET /plans` and `GET /plans/{id}`, which hold the owner's request text)
are owner-only too, unless the site is a public demo (`PUBLIC_DEMO=true`); then anyone may read
plans and the public web UI's origin (`PUBLIC_UI_URL`) may watch `/ws`. An `Origin` header is easy
to forge outside a browser, so it never grants `/ws` access on its own.

Evidence: the chain itself (`GET /evidence/{day}/chain`, every record and payload) is owner-only.
The public verify page gets `GET /evidence/{day}/hashes` (record hashes only) and
`GET /evidence/{day}/root` (the current and the committed Merkle root), so anyone can check that
the day's log still reproduces the root committed to git without seeing a single record.

Incident reports (add-on 5.2, owner-only): `GET /incidents?day=` lists a day's events,
`GET /incidents/{event_id}` returns one event's report (clause, rule, measurement, every chained
verdict, action, ack and label, the chain status), and `GET /incidents/{event_id}/report`
downloads it as a PDF (`evidence.incident`), or as printable HTML when Chromium is not installed.

Response (WS1b): `services_from_settings` builds the policy, firewall and actions once
(`runtime.response`), mounts the alerts PWA routes (`/alerts`, `/ack`, `/interlock/{device}/resume`,
owner-only), ticks the escalation timers once a second, and serves how the response side is
configured (Tier 2, firewall, dry run, interlock) in `GET /runtime/metrics` under `response`.
Upheld disputes, owner corrections and responders' *False alarm* taps become permanent Rule CI
regressions (WS1f). With `TELEGRAM_BOT_TOKEN`, a poller long-polls the Telegram bot for taps and
the owner mints role links at `GET /telegram/link?role=…` (add-on 5.1). With `RULECAM_WEBHOOKS`,
another poller sends the signed outbound webhooks (add-on 5.4).

Cost (WS3): with the runtime on, a `CostWatch` ticks once a second: the per-camera dollar budget
(alarm and degrade, `runtime.cost`), the cost per camera-hour, and the optional idle GPU stop
(`runtime.idle_stop`), all served under `cost` in `GET /runtime/metrics` and in `GET /metrics`.

`make api` serves the module-level `app`, built from settings with the replay cache, the bus, the
evidence chain, perception and Pair-a-Phone; `RULECAM_RUNTIME=true` also starts the runtime loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    params,
    status,
)
from fastapi import Path as PathParam
from fastapi.responses import PlainTextResponse
from fastapi.responses import Response as HttpResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from rulecam.act.api import FalseAlarmSink
from rulecam.act.drills import DrillRunner, drills_router
from rulecam.advisor.api import AdviceStore, PlanFactory, advice_router
from rulecam.api.ask import ask_router
from rulecam.api.auth import ClientAddress, OwnerAuth
from rulecam.api.crowd import crowd_router
from rulecam.api.plans import PlannerRunner, PlanRegistry, QuestionsService, plans_router
from rulecam.api.prometheus import (
    HealthProbe,
    MetricsSources,
    VerdictCounter,
    dependency_probes,
    metrics_router,
    serverless_gpu_rate,
)
from rulecam.api.site_tools import build_site_tools
from rulecam.api.ws import mirror_bus
from rulecam.ask import (
    AskAgent,
    AskLimits,
    CaptionMemory,
    FrameGrabber,
    camera_names,
    open_tables,
    planner_profiles,
)
from rulecam.ask.data import CrowdFiles
from rulecam.ask.identity import IdentityMemory, gpu_embedder
from rulecam.ask.review import SparkOpenReviewer
from rulecam.ask.runlog import AskRunLog, git_sha
from rulecam.ask.watch import ClipJoiner
from rulecam.bus import Bus, Topic
from rulecam.capture.pair_phone import PairPhone, build_router, require_owner
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted import SparkClient, VoiceTranscribeClient
from rulecam.clients.open_weights.glimmer import GlimmerClient
from rulecam.clients.open_weights.spark_open import SparkOpenClient
from rulecam.clock import SystemClock
from rulecam.config import Settings, get_settings
from rulecam.contracts import TTS, Calibration, Chunk, Clock, PrivacyMode, SitePlan
from rulecam.crowd.memory import Calibrations, CrowdMemory, ModelCalibration
from rulecam.crowd.scan import CrowdScanner
from rulecam.crowd.warden import CrowdWarden
from rulecam.crowd.zones import CrowdZone
from rulecam.evidence.chain import ChainReport, EventChain, read_chain, verify_chain
from rulecam.evidence.incident import (
    IncidentReport,
    IncidentSummary,
    PdfPrinter,
    build_incident,
    candidates,
    find_incident,
    render_incident_html,
    report_document,
)
from rulecam.evidence.merkle import chain_root, read_day_roots
from rulecam.evidence.packs import Thumbnail, render_pdf
from rulecam.log import get_logger
from rulecam.perception.correction import CorrectionService
from rulecam.perception.correction_api import correction_router
from rulecam.perception.metrics import PerceptionMetrics, PerceptionMetricsReport
from rulecam.perception.router import Purpose
from rulecam.planner.errors import PipelineError
from rulecam.planner.geometry import model_calibration
from rulecam.planner.pipeline import PlannerPipeline, PlanStore
from rulecam.planner.runner import ChunkFrames, PlannerService
from rulecam.planner.site_config import SiteConfigError, load_site_config
from rulecam.planner.standards import StandardsLibrary
from rulecam.planner.steps.audit import audit_hook
from rulecam.replay.cache import Mode, ModeChanged, ReplayCache, badge_for
from rulecam.rule_ci import prove_plan_hook
from rulecam.rule_ci.hard_negatives import HardNegatives
from rulecam.runtime.activate import Activation, PlanActivator, restore_activation
from rulecam.runtime.cost import CostMeter, CostReport, CostSettings, CostWatch
from rulecam.runtime.factory import (
    ChainEventSource,
    ChunkIndex,
    Perception,
    build_perception,
    build_runtime,
    build_tier0,
    build_tier1,
    crop_segmenter,
    geometry_estimator,
)
from rulecam.runtime.idle_stop import IdleGpuStop
from rulecam.runtime.loop import ActivePlan, LoopMetrics, RuntimeLoop, plan_concepts
from rulecam.runtime.response import Response, ResponseStatus, build_response
from rulecam.runtime.serverless import GpuHealth, RunPodServerless
from rulecam.verify.tier2 import SparkOpenVerifier
from rulecam.voice_out.tts import MmsTts
from rulecam.worker import DisputeService, worker_router
from rulecam.worker.disputes import DisputeReviewer

_log = get_logger(__name__)


@dataclass
class AppServices:
    """Everything the API serves, injected.

    Attributes:
        bus: The event bus mirrored on `/ws`.
        cache: The replay cache (its mode drives the LIVE/REPLAY badge).
        chain_root: Directory of per-site chain folders.
        site: The site this server runs.
        runtime: The orchestrator; `None` serves the API without processing chunks.
        routers: Routers of other packages to mount.
        perception_metrics: Served at `/metrics/perception`.
        pair: Pair-a-Phone (P1-09); mounted at `/pair` when set.
        correction: The correction loop (P1-10); mounted at `/correction` when set.
        chunks: Index of recent chunks, fed from `chunk.ready` (feeds `/cameras`).
        plans: The `/plans` registry.
        verify_dir: Directory served at `/verify` (the packaged browser verify page in
            production, P3-07b); `None` skips it.
        tickers: Called once a second off the event loop (e.g. `EscalationManager.tick`).
        pollers: Blocking calls run back to back, each in its own worker thread until shutdown
            (e.g. `TelegramBot.poll`, which long-polls); each must wait on its own between calls.
        uploads_dir: Where planner uploads are stored (`POST /plans/uploads`).
        planner: The Site Planner runner (P2-06); `POST /plans/requests` needs it.
        activator: Activates approved plans in the runtime; approval answers 503 without one.
        viewer_origins: Browser origins (e.g. the public web UI) that may watch the read-only
            `/ws` mirror without being the owner, on a public demo only. Nothing that changes
            state accepts them without an owner session.
        auth: Owner login (`rulecam.api.auth`); `None` means loopback only, with login off.
        questions: The planner's questions and re-runs (`PlannerService`).
        verdicts: Verdict counts for `GET /metrics`, fed from `verdict.new`.
        health_probes: Dependencies (Glimmer, SAM) whose state `GET /metrics` and `GET /health`
            show.
        response: How the policy, firewall and actions are configured (`/runtime/metrics`).
        cost: The dollar budget's report (`/runtime/metrics` → `cost`, `/metrics`).
        advice: Checked Site Advisor cards served at `/advice` (P3-10).
        plan_factory: P4's measurement-plan builder; `POST /advice/{id}/accept` needs it.
        public_demo: Demo sites only: non-owners may watch `/ws` (from a viewer origin) and read
            plans. Off by default; a real site keeps those reads owner-only.
        stills_root: Where the runtime writes evidence strips; incident reports inline stills
            from under it only.
        verify_base: The verify page's public URL in incident reports' QR codes (e.g.
            `<PUBLIC_UI_URL>/rulecam-api/verify/`); default the API's own `/verify/`.
        pdf_printer: Prints a report to PDF (`evidence.packs.render_pdf`).
    """

    bus: Bus
    cache: ReplayCache
    chain_root: Path
    site: str
    runtime: RuntimeLoop | None = None
    roots_dir: Path = Path("evidence/roots")
    routers: Sequence[APIRouter] = field(default_factory=tuple)
    perception_metrics: PerceptionMetrics = field(default_factory=PerceptionMetrics)
    pair: PairPhone | None = None
    correction: CorrectionService | None = None
    chunks: ChunkIndex | None = None
    plans: PlanRegistry = field(default_factory=PlanRegistry)
    verify_dir: Path | None = None
    tickers: Sequence[Callable[[], object]] = field(default_factory=tuple)
    pollers: Sequence[Callable[[], object]] = field(default_factory=tuple)
    uploads_dir: Path | None = None
    planner: PlannerRunner | None = None
    activator: PlanActivator | None = None
    viewer_origins: frozenset[str] = frozenset()
    auth: OwnerAuth | None = None
    questions: QuestionsService | None = None
    verdicts: VerdictCounter = field(default_factory=VerdictCounter)
    health_probes: Sequence[HealthProbe] = ()
    response: ResponseStatus | None = None
    cost: Callable[[], CostReport] | None = None
    advice: AdviceStore = field(default_factory=AdviceStore)
    plan_factory: PlanFactory | None = None
    public_demo: bool = False
    revision: str | None = None
    stills_root: Path | None = None
    verify_base: str | None = None
    pdf_printer: PdfPrinter = render_pdf


class RuntimeReport(LoopMetrics):
    """Body of `GET /runtime/metrics`: the loop's metrics, the response side and the spend."""

    response: ResponseStatus | None = None
    cost: CostReport | None = None


TICK_S = 1.0


async def _tick_forever(tickers: Sequence[Callable[[], object]]) -> None:
    """Call every ticker once a second in a worker thread until cancelled."""
    while tickers:
        for tick in tickers:
            try:
                await asyncio.to_thread(tick)
            except Exception:  # one failing ticker must not stop the escalation timers
                _log.exception("ticker_failed", ticker=getattr(tick, "__qualname__", repr(tick)))
        await asyncio.sleep(TICK_S)


async def _poll_forever(poller: Callable[[], object]) -> None:
    """Call a blocking poller back to back in a worker thread until cancelled."""
    while True:
        try:
            await asyncio.to_thread(poller)
        except Exception:  # a failing poll must not end the loop
            _log.exception("poller_failed", poller=getattr(poller, "__qualname__", repr(poller)))
            await asyncio.sleep(TICK_S)


VERIFY_PAGE_DIR = Path(__file__).resolve().parent.parent / "evidence" / "verify_page"
# Written next to `src/` by `make ship-api` (scripts/deploy/ship_api.sh): the git SHA deployed.
REVISION_FILE = Path(__file__).resolve().parents[3] / "REVISION"


def code_revision(configured: str | None, revision_file: Path = REVISION_FILE) -> str | None:
    """The git revision this API runs: `RULECAM_REVISION`, else the shipped `REVISION` file.

    Args:
        configured: `RULECAM_REVISION` (e.g. baked into an image).
        revision_file: The file `make ship-api` writes at the release root.

    Returns:
        The revision, or `None` when neither says (a dev checkout).
    """
    if configured:
        return configured.strip()
    try:
        lines = revision_file.read_text(encoding="utf-8").split()
    except OSError:
        return None
    return lines[0] if lines else None


class DayRoot(BaseModel):
    """Body of `GET /evidence/{day}/root`: the current and the committed Merkle root.

    `committed_n` is how many records the committed root covers: a chain that is still growing
    must reproduce it over its first `committed_n` records.
    """

    root: str
    n: int
    committed_root: str | None
    committed_n: int | None


class DayHashes(BaseModel):
    """Body of `GET /evidence/{day}/hashes`: the day's record hashes in order, nothing else."""

    n: int
    hashes: list[str]


class ModeRequest(BaseModel):
    """Body of `PUT /mode`."""

    mode: Mode


class CameraInfo(BaseModel):
    """One row of `GET /cameras`."""

    camera_id: str
    label: str
    source: str
    last_chunk_at: datetime | None = None


class Health(BaseModel):
    """Body of `GET /health`.

    `gpus` is each GPU dependency's state (`runtime.serverless`): a serverless GPU scaled to
    zero is `cold-ready` (up; the UI says "GPU waking" while a request starts it), not down.
    `revision` is the git SHA deployed (`code_revision`), which `make ship-api` waits for.
    """

    ok: bool
    site: str
    mode: Mode
    runtime: bool
    revision: str | None = None
    gpus: dict[str, GpuHealth] = {}


def _chain_file(services: AppServices, day: date) -> Path:
    path = services.chain_root / services.site / f"{day.isoformat()}.jsonl"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no chain for {day.isoformat()}")
    return path


_OWNER = [Depends(require_owner)]


def _content_reads(services: AppServices) -> list[params.Depends]:
    """Authentication for reads that expose site content: none on a public demo, else owner."""
    return [] if services.public_demo else _OWNER


def _core_router(services: AppServices, auth: OwnerAuth) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> Health:
        return Health(
            ok=True,
            site=services.site,
            mode=services.cache.mode,
            runtime=services.runtime is not None,
            revision=services.revision,
            gpus={probe.service: probe.health() for probe in services.health_probes},
        )

    @router.get("/mode")
    def get_mode() -> ModeChanged:
        mode = services.cache.mode
        return ModeChanged(mode=mode, badge=badge_for(mode))

    @router.put("/mode", dependencies=_OWNER)
    def set_mode(body: ModeRequest) -> ModeChanged:
        services.cache.set_mode(body.mode)
        return ModeChanged(mode=body.mode, badge=badge_for(body.mode))

    @router.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        public_viewer = (
            services.public_demo and websocket.headers.get("origin") in services.viewer_origins
        )
        if not public_viewer:
            try:
                auth.require_owner(websocket)
            except HTTPException:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
        await mirror_bus(websocket, services.bus)

    @router.get("/metrics/perception", tags=["metrics"])
    def perception_metrics() -> PerceptionMetricsReport:
        return services.perception_metrics.report()

    @router.get("/cameras", tags=["cameras"])
    def cameras() -> list[CameraInfo]:
        return _cameras(services)

    return router


def _cameras(services: AppServices) -> list[CameraInfo]:
    seen = services.chunks.last_seen() if services.chunks else {}
    rows = {
        cam.camera_id: CameraInfo(
            camera_id=cam.camera_id,
            label=cam.label,
            source="paired_phone",
            last_chunk_at=seen.get(cam.camera_id),
        )
        for cam in (services.pair.registry.cameras() if services.pair else [])
    }
    for camera_id, last in sorted(seen.items()):
        rows.setdefault(
            camera_id,
            CameraInfo(camera_id=camera_id, label=camera_id, source="capture", last_chunk_at=last),
        )
    return list(rows.values())


def _chain_days(folder: Path) -> list[date]:
    days = []
    for path in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        try:
            days.append(date.fromisoformat(path.stem))
        except ValueError:
            _log.warning("evidence_unexpected_file", name=path.name)
    return days


def _evidence_router(services: AppServices) -> APIRouter:
    router = APIRouter(prefix="/evidence", tags=["evidence"])

    @router.get("/days")
    def days() -> list[date]:
        return _chain_days(services.chain_root / services.site)

    @router.get("/{day}/chain", response_class=PlainTextResponse, dependencies=_OWNER)
    def chain(day: date) -> PlainTextResponse:
        text = _chain_file(services, day).read_text(encoding="utf-8")
        return PlainTextResponse(
            text, media_type="application/x-ndjson", headers={"Cache-Control": "no-store"}
        )

    @router.get("/{day}/hashes")
    def hashes(day: date) -> DayHashes:
        try:
            found = [record.hash for record in read_chain(_chain_file(services, day))]
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "the chain file is unreadable") from exc
        return DayHashes(n=len(found), hashes=found)

    @router.get("/{day}/verify")
    def verify(day: date) -> ChainReport:
        return verify_chain(_chain_file(services, day))

    @router.get("/{day}/root")
    def root(day: date) -> DayRoot:
        current, n = chain_root(_chain_file(services, day))
        roots_file = services.roots_dir / f"{day.isoformat()}.txt"
        committed = read_day_roots(roots_file).get(services.site) if roots_file.is_file() else None
        return DayRoot(
            root=current,
            n=n,
            committed_root=committed[0] if committed else None,
            committed_n=committed[1] if committed else None,
        )

    return router


EventIdPath = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]


def _incident(
    services: AppServices, event_id: str, request: Request
) -> tuple[IncidentReport, tuple[Thumbnail, ...]]:
    found = find_incident(services.chain_root / services.site, event_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no incident with this event id")
    day, records = found
    plan = services.runtime.plan if services.runtime is not None else None
    base = services.verify_base or f"{request.base_url}verify/"
    return build_incident(
        records,
        event_id,
        site=services.site,
        day=day,
        privacy_mode=plan.privacy_mode if plan is not None else PrivacyMode.STRICT,
        verify_url=f"{base}?day={day.isoformat()}",
        chain=verify_chain(services.chain_root / services.site / f"{day.isoformat()}.jsonl"),
        stills_root=services.stills_root,
    )


def _incidents_router(services: AppServices) -> APIRouter:
    router = APIRouter(prefix="/incidents", tags=["incidents"], dependencies=_OWNER)

    @router.get("")
    def incidents(day: date | None = None) -> list[IncidentSummary]:
        days = _chain_days(services.chain_root / services.site)
        chosen = day or (days[-1] if days else None)
        if chosen is None or chosen not in days:
            return []
        return candidates(read_chain(_chain_file(services, chosen)))

    @router.get("/{event_id}")
    def incident(event_id: EventIdPath, request: Request) -> IncidentReport:
        return _incident(services, event_id, request)[0]

    @router.get("/{event_id}/report", response_class=HttpResponse)
    def report(
        event_id: EventIdPath, request: Request, format: Literal["pdf", "html"] = "pdf"
    ) -> HttpResponse:
        html = render_incident_html(*_incident(services, event_id, request))
        if format == "pdf":
            body, media_type = report_document(html, services.pdf_printer)
        else:
            body, media_type = html.encode("utf-8"), "text/html"
        extension = "pdf" if media_type == "application/pdf" else "html"
        return HttpResponse(
            body,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="incident-{event_id}.{extension}"',
                "Cache-Control": "no-store",
                "X-Rulecam-Report-Format": extension,
            },
        )

    return router


def _runtime_router(services: AppServices) -> APIRouter:
    router = APIRouter(prefix="/runtime", tags=["runtime"])

    def runtime() -> RuntimeLoop:
        if services.runtime is None:
            raise HTTPException(status_code=503, detail="runtime loop not running")
        return services.runtime

    @router.put("/plan", dependencies=_OWNER)
    def set_plan(plan: ActivePlan) -> dict[str, int]:
        runtime().set_plan(plan)
        return {"rules": len(plan.rules)}

    @router.get("/metrics")
    def metrics() -> RuntimeReport:
        cost = services.cost() if services.cost is not None else None
        return RuntimeReport(**dict(runtime().metrics), response=services.response, cost=cost)

    return router


def _metrics_sources(services: AppServices) -> MetricsSources:
    return MetricsSources(
        runtime=lambda: services.runtime.metrics if services.runtime is not None else None,
        perception=services.perception_metrics.report,
        verdicts=services.verdicts,
        probes=services.health_probes,
        cost=lambda: services.cost() if services.cost is not None else None,
    )


def _routers(services: AppServices, auth: OwnerAuth) -> list[APIRouter]:
    routers = [
        auth.router(),
        _core_router(services, auth),
        metrics_router(_metrics_sources(services)),
        _evidence_router(services),
        _incidents_router(services),
        _runtime_router(services),
        plans_router(
            services.plans,
            services.bus,
            uploads_dir=services.uploads_dir,
            planner=services.planner,
            activator=services.activator,
            questions=services.questions,
            read_dependencies=_content_reads(services),
        ),
    ]
    clock = SystemClock()
    routers.append(
        advice_router(
            services.advice,
            EventChain(services.chain_root, services.site, clock=clock),
            services.bus,
            clock,
            plan_factory=services.plan_factory,
        )
    )
    if services.pair is not None:
        routers.append(build_router(services.pair))
    if services.correction is not None:
        routers.append(correction_router(services.correction, services.bus, dependencies=_OWNER))
    return [*routers, *services.routers]


def _lifespan(
    services: AppServices,
) -> Callable[[FastAPI], contextlib.AbstractAsyncContextManager[None]]:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        services.bus.bind_loop(asyncio.get_running_loop())
        tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(services.verdicts.consume(services.bus))
        ]
        if services.chunks is not None:
            tasks.append(asyncio.create_task(_index_chunks(services.bus, services.chunks)))
        if services.runtime is not None:
            tasks.append(asyncio.create_task(services.runtime.run()))
        if services.tickers:
            tasks.append(asyncio.create_task(_tick_forever(services.tickers)))
        tasks.extend(asyncio.create_task(_poll_forever(p)) for p in services.pollers)
        for task in tasks:
            task.add_done_callback(_log_task_end)
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            services.bus.bind_loop(None)

    return lifespan


def create_app(services: AppServices) -> FastAPI:
    """Build the API around `services`.

    Args:
        services: The injected bus, cache, chain location, runtime, owner login and extra routers.

    Returns:
        The FastAPI app; every owner route takes `services.auth` (loopback, or an owner
        session). On start-up it binds the bus to the event loop (so worker threads can
        publish) and starts the runtime loop if there is one; on shutdown it stops it.
    """
    app = FastAPI(title="Rulecam", version="0.1.0", lifespan=_lifespan(services))
    auth = services.auth or OwnerAuth(clock=SystemClock(), allowed_origins=services.viewer_origins)
    app.dependency_overrides[require_owner] = auth.require_owner
    for router in _routers(services, auth):
        app.include_router(router)
    if services.verify_dir is not None:
        services.verify_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/verify", StaticFiles(directory=services.verify_dir, html=True), name="verify")
    return app


async def _index_chunks(bus: Bus, index: ChunkIndex) -> None:
    async with contextlib.aclosing(bus.subscribe(Topic.CHUNK_READY)) as messages:
        async for message in messages:
            if isinstance(message.payload, Chunk):
                index.record(message.payload)


def _log_task_end(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        _log.error("background_task_died", task=task.get_name(), error=repr(error))


PLANS_DIR = Path("plans")
ACTIVE_PLAN_FILE = Path("runtime") / "active_plan.json"


def run_calibrations(store: PlanStore) -> Callable[[SitePlan], dict[str, Calibration]]:
    """Find the ground calibrations a plan's planner run used (`PlanStore.calibrations`).

    That is the owner's site-config calibrations (`plans/<id>/input.json`) over the ones the
    survey fitted to reference objects in the scene (`plans/<id>/steps/02_survey.json`). A
    `SitePlan` holds no calibration, so this is the only place one can come from; a plan without
    a saved run (e.g. one posted to `POST /plans`) has none.
    """

    def lookup(plan: SitePlan) -> dict[str, Calibration]:
        try:
            return store.calibrations(plan.plan_id)
        except PipelineError:
            return {}

    return lookup


def build_planner(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    perception: Perception,
    plans: PlanRegistry,
    site: str,
) -> PlannerService | None:
    """Build the Site Planner runner for `POST /plans/requests` (P2-06a).

    Spark, Voice Transcribe and hosted SAM all go through the replay cache, and so do cited
    standards pages (`<data>/standards_cache`, for `citation_text_match`). Step 9 audits the plan
    with Spark Open and back-translates it with Glimmer (P2-07); either being unreachable marks
    the plan `audit_skipped` or `not back-translated` instead of failing it. Plans are saved
    under `plans/`, survey and probe frames come from the last `planner_survey_window_s` (60 s)
    of chunks, a few per chunk, and finished plans go into the `/plans` registry. The site's
    name, privacy mode, roles, languages, calibrations and zones come from the owner's site config
    (`RULECAM_SITE_CONFIG`); without one, the defaults (`site`, Standard privacy, nothing else).

    Returns:
        The runner, or `None` (logged) when the clients cannot be built, e.g. in live mode
        without API keys, or when the site config is invalid; `POST /plans/requests` then
        answers 503.
    """
    try:
        site_config = load_site_config(settings.rulecam_site_config)
    except SiteConfigError as exc:
        _log.error("planner_disabled", error=str(exc))
        return None
    if settings.rulecam_site_config is None or not settings.rulecam_site_config.is_file():
        site_config = site_config.model_copy(update={"site_name": site})
    try:
        spark = SparkClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
        voice = VoiceTranscribeClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    except ClientConfigError as exc:
        _log.warning("planner_disabled", error=str(exc))
        return None
    spark_open = SparkOpenClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
    glimmer = GlimmerClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
    pipeline = PlannerPipeline(
        spark=spark,
        files=spark,
        segmenter=perception.hosted,
        voice=voice,
        bus=bus,
        grounding=spark,
        standards=StandardsLibrary.from_settings(settings),
        store=PlanStore(PLANS_DIR),
        audit=audit_hook(SparkOpenVerifier(spark_open), glimmer, glimmer_model=glimmer.model),
        prove=prove_plan_hook(bus, calibrations=run_calibrations(PlanStore(PLANS_DIR))),
        zoom=crop_segmenter(settings, perception),
        geometry=geometry_estimator(settings, perception),
    )
    frames = ChunkFrames(
        perception.chunks,
        clock=clock,
        out_dir=settings.rulecam_data_dir / "planner_frames",
        window_s=settings.planner_survey_window_s,
    )
    return PlannerService(pipeline, site=site_config, frames=frames, on_plan=plans.put, clock=clock)


def restore_approved(
    plans: PlanRegistry, store: PlanStore, activation: Activation | None
) -> SitePlan | None:
    """After a restart, list the plan the runtime enforces again, as approved (`GET /plans`).

    The registry lives in memory; without this, a restarted API keeps enforcing the approved
    plan (`restore_activation`) while `/plans` and the UI show no plan at all.

    Returns:
        The restored plan, or `None` when there is no activation or its saved plan is missing,
        invalid or a different version (logged).
    """
    if activation is None:
        return None
    try:
        plan = store.load_plan(activation.plan_id)
    except (PipelineError, ValueError) as exc:
        _log.warning("approved_plan_unreadable", plan_id=activation.plan_id, error=str(exc))
        return None
    if plan is None or plan.version != activation.version:
        _log.warning(
            "approved_plan_not_restored",
            plan_id=activation.plan_id,
            version=activation.version,
            saved_version=plan.version if plan is not None else None,
        )
        return None
    plans.put(plan)
    plans.approve(plan.plan_id)
    _log.info("approved_plan_restored", plan_id=plan.plan_id, version=plan.version)
    return plan


def find_plan(plans: PlanRegistry, store: PlanStore) -> Callable[[str], SitePlan | None]:
    """Look a plan up in the registry first, then among the saved plans (`plans/`).

    Returns:
        A lookup giving `None` for an unknown or unsafe plan id.
    """

    def lookup(plan_id: str) -> SitePlan | None:
        try:
            return plans.get(plan_id)[0]
        except HTTPException:
            pass
        try:
            return store.load_plan(plan_id)
        except PipelineError:
            _log.warning("worker_plan_unreadable", plan_id=plan_id)
            return None

    return lookup


def build_worker_router(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    chain: EventChain,
    plans: PlanRegistry,
    client_address: ClientAddress | None = None,
    reviewer: DisputeReviewer | None = None,
    hard_negatives: HardNegatives | None = None,
) -> APIRouter:
    """Build `/worker` (P2-11).

    Disputes are re-reviewed by `reviewer` (the runtime's policy, which sends them to Tier 2 and
    leaves them pending for a person without it), else by Spark Open through the replay cache.
    Upheld ones become Rule CI regressions through `hard_negatives`.
    """
    if reviewer is None:
        spark_open = SparkOpenClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
        reviewer = SparkOpenVerifier(spark_open)
    disputes = DisputeService(chain, reviewer, clock=clock, hard_negatives=hard_negatives)
    return worker_router(
        plans=find_plan(plans, PlanStore(PLANS_DIR)),
        chain=chain,
        disputes=disputes,
        clock=clock,
        public_base_url=settings.public_ui_url,
        client_address=client_address or ClientAddress(),
    )


def services_from_settings(settings: Settings, site: str = "demo") -> AppServices:
    """Build the default services from settings.

    Always: bus, replay cache, evidence chain, perception (SAM router, metrics, track store),
    Pair-a-Phone, the correction loop, the plan registry and the worker page (`/worker`,
    P2-11), plus the Site Planner runner when
    its clients can be built. With `RULECAM_RUNTIME=true`, also the runtime loop.

    Args:
        settings: Application settings.
        site: Site name for the chain folder.

    Returns:
        The services.
    """
    bus = Bus()
    clock = SystemClock()
    cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
    chain_root = settings.rulecam_data_dir / "evidence" / "chain"
    chain = EventChain(chain_root, site, clock=clock)
    perception = build_perception(settings, cache=cache, clock=clock, bus=bus)
    probes = dependency_probes(
        settings.glimmer_base_url,
        settings.sam_base_url,
        glimmer_runpod=RunPodServerless.for_service(settings, settings.glimmer_edge_key).fetch,
        sam_runpod=RunPodServerless.for_service(settings, settings.sam_edge_key).fetch,
    )
    glimmer = next((probe.up for probe in probes if probe.service == "glimmer"), None)
    response = build_response(
        settings,
        tier1=build_tier1(
            settings,
            cache=cache,
            clock=clock,
            bus=bus,
            tier0=build_tier0(settings, perception),
            probe=glimmer,
        ),
        segmenter=perception.hosted,
        cache=cache,
        clock=clock,
        bus=bus,
        chain=chain,
    )
    meter = CostMeter(
        CostSettings.from_settings(settings),
        usage=perception.metrics.hosted_usage,
        gpu_rate=serverless_gpu_rate(probes, settings.rulecam_gpu_worker_usd_per_hour),
    )
    runtime = (
        build_runtime(
            settings,
            perception,
            cache=cache,
            clock=clock,
            bus=bus,
            chain=chain,
            response=response,
            cost=meter,
        )
        if settings.rulecam_runtime
        else None
    )
    watch = cost_watch(settings, meter, runtime, perception.chunks, clock=clock)
    hard_negatives = HardNegatives(settings.regressions_dir)

    def concepts() -> list[str]:
        plan = runtime.plan if runtime is not None else None
        return plan_concepts(plan) if plan is not None else []

    false_alarm = false_alarm_regressions(lambda: runtime.plan if runtime else None, hard_negatives)

    pair = PairPhone(
        bus=bus, clock=clock, segmenter=perception.hosted, settings=settings, concepts=concepts
    )
    correction = CorrectionService(
        segmenter=perception.router.for_purpose(Purpose.RUNTIME),
        store=perception.store,
        events=ChainEventSource(chain_root, site),
        chunks=perception.chunks,
        record_label=chain.append,
        hard_negatives=hard_negatives,
    )
    active_plan_path = settings.rulecam_data_dir / ACTIVE_PLAN_FILE
    restored = restore_activation(runtime, active_plan_path) if runtime is not None else None
    activator = PlanActivator(
        runtime=runtime,
        chain=chain,
        state_path=active_plan_path,
        calibrations=run_calibrations(PlanStore(PLANS_DIR)),
    )
    plans = PlanRegistry()
    restore_approved(plans, PlanStore(PLANS_DIR), restored)
    planner = build_planner(
        settings, cache=cache, clock=clock, bus=bus, perception=perception, plans=plans, site=site
    )
    crowd, warden, crowd_tickers = build_crowd(
        settings,
        cache=cache,
        clock=clock,
        bus=bus,
        perception=perception,
        response=response,
        runtime=runtime,
    )
    ask_agent, ask_tickers = build_ask(
        settings, cache=cache, clock=clock, bus=bus, perception=perception, crowd=(crowd, warden)
    )
    origins = viewer_origins(settings.public_ui_url)
    auth = OwnerAuth.from_settings(settings, clock=clock, allowed_origins=origins)
    client_address = ClientAddress(
        settings.ui_proxy_secret.get_secret_value() if settings.ui_proxy_secret else None
    )
    return AppServices(
        bus=bus,
        cache=cache,
        chain_root=chain_root,
        site=site,
        runtime=runtime,
        perception_metrics=perception.metrics,
        pair=pair,
        correction=correction,
        chunks=perception.chunks,
        verify_dir=VERIFY_PAGE_DIR,
        uploads_dir=settings.rulecam_data_dir / "uploads",
        plans=plans,
        planner=planner,
        questions=planner,
        activator=activator,
        viewer_origins=origins,
        public_demo=settings.public_demo,
        revision=code_revision(settings.rulecam_revision),
        stills_root=settings.rulecam_data_dir / "strips",
        verify_base=(
            f"{settings.public_ui_url.rstrip('/')}/rulecam-api/verify/"
            if settings.public_ui_url
            else None
        ),
        auth=auth,
        health_probes=probes,
        tickers=(
            *response.tickers,
            *((watch.tick,) if watch is not None else ()),
            *ask_tickers,
            *crowd_tickers,
        ),
        pollers=response.pollers(false_alarm),
        response=response.status,
        cost=watch.report if watch is not None else None,
        routers=(
            build_worker_router(
                settings,
                cache=cache,
                clock=clock,
                bus=bus,
                chain=chain,
                plans=plans,
                client_address=client_address,
                reviewer=response.policy,
                hard_negatives=hard_negatives,
            ),
            owner_alerts_router(response, on_false_alarm=false_alarm),
            ask_router(ask_agent, dependencies=_OWNER),
            crowd_router(
                crowd,
                warden,
                camera_names=camera_names(settings.rulecam_camera_names),
                dependencies=_OWNER,
            ),
            *build_site_tools(
                settings,
                cache=cache,
                clock=clock,
                bus=bus,
                perception=perception,
                site_name=site,
                dependencies=_OWNER,
            ),
            *((webhook,) if (webhook := response.webhook_router(false_alarm)) is not None else ()),
            *((links,) if (links := response.telegram_links_router(_OWNER)) is not None else ()),
        ),
    )


def build_ask(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    perception: Perception,
    crowd: tuple[CrowdMemory | None, CrowdWarden | None] = (None, None),
) -> tuple[AskAgent | None, tuple[Callable[[], object], ...]]:
    """The `POST /ask` agent and its memories' tickers: Spark plans, SAM counts, Glimmer looks.

    Served in prototype mode only: it reads per-track spans (dwell time per tracked person). The
    caption memory runs when `RULECAM_ASK_CAPTION_EVERY_S` is above 0; the identity memory
    (re-identification on the GPU pod: OSNet-AIN for people, DINOv2 for objects) when
    `SAM_BASE_URL` and `SAM_API_KEY` are set. Every answer is appended to the hash-chained run log
    `<data>/ask/runs.jsonl`. With crowd watch on (`crowd`), the crowd tables and `crowd_scan` are
    Spark's too. With `RULECAM_ASK_REVIEW` and a Baseten key, Muse Spark Open (the
    Tier 2 client) reviews each draft answer and can send it back once. Its `watch` tool joins
    recorded chunks into one clip for Spark (`muse-spark-1.2`) to watch as video.

    Returns:
        The agent and the tickers; `(None, ())` (logged) outside prototype mode or without a
        Spark key, and `/ask` then answers 503.
    """
    if not settings.rulecam_prototype_mode:
        return None, ()
    try:
        spark = SparkClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    except ClientConfigError as exc:
        _log.warning("ask_disabled", error=str(exc))
        return None, ()
    glimmer = GlimmerClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
    ask_dir = settings.rulecam_data_dir / "ask"
    frames = FrameGrabber(perception.chunks, ask_dir / "frames")
    names = camera_names(settings.rulecam_camera_names)
    every = settings.rulecam_ask_caption_every_s
    memory = (
        CaptionMemory(
            frames=frames,
            glimmer=glimmer,
            glimmer_model=glimmer.model,
            path=ask_dir / "captions.jsonl",
            every_s=every,
            clock=clock,
            site_profile=settings.rulecam_site_profile,
            camera_names=names,
        )
        if every > 0
        else None
    )
    identity = None
    if settings.rulecam_ask_identities and settings.sam_base_url and settings.sam_api_key:
        base_url, key = settings.sam_base_url, settings.sam_api_key.get_secret_value()
        edge = settings.sam_edge_key.get_secret_value() if settings.sam_edge_key else None
        identity = IdentityMemory(
            tracks_root=settings.rulecam_data_dir / "tracks",
            frames=frames,
            embed=gpu_embedder(base_url, key, edge_key=edge),
            embed_person=(
                gpu_embedder(base_url, key, edge_key=edge, model="person")
                if settings.rulecam_ask_person_reid
                else None
            ),
            glimmer=glimmer,
            glimmer_model=glimmer.model,
            out_dir=ask_dir / "identity",
            site_profile=settings.rulecam_site_profile,
        )
    reviewer = None
    if settings.rulecam_ask_review and settings.baseten_api_key is not None:
        reviewer = SparkOpenReviewer(
            SparkOpenClient.from_settings(settings, cache=cache, clock=clock, bus=bus),
            max_result_chars=settings.rulecam_ask_max_result_chars,
        )
    agent = AskAgent(
        spark=spark,
        segmenter=perception.hosted,
        glimmer=glimmer,
        glimmer_model=glimmer.model,
        tables=open_tables(
            settings.rulecam_data_dir / "tracks",
            ask_dir / "captions.jsonl",
            identity.identities_path if identity is not None else None,
            identity.notes_path if identity is not None else None,
            crowd=crowd_files(*crowd),
            max_rows=settings.rulecam_ask_max_rows,
        ),
        frames=frames,
        clock=clock,
        timezone=settings.rulecam_site_timezone,
        site_profile=settings.rulecam_site_profile,
        camera_names=names,
        camera_profiles=planner_profiles(PLANS_DIR),
        caption_every_s=every,
        marks_dir=ask_dir / "marked",
        limits=AskLimits(
            max_rows=settings.rulecam_ask_max_rows,
            max_result_chars=settings.rulecam_ask_max_result_chars,
            max_frames=settings.rulecam_ask_max_frames,
            max_actions=settings.rulecam_ask_max_actions,
        ),
        runs=AskRunLog(
            ask_dir / "runs.jsonl",
            clock=clock,
            git_sha=git_sha(settings.rulecam_git_sha),
            max_result_chars=settings.rulecam_ask_max_result_chars,
        ),
        reviewer=reviewer,
        clips=ClipJoiner(
            perception.chunks, ask_dir / "clips", max_seconds=settings.rulecam_ask_max_watch_s
        ),
        files=spark,
        watch_model=settings.rulecam_ask_watch_model,
        crowd=crowd[0],
    )
    return agent, tuple(m.tick for m in (memory, identity) if m is not None)


def crowd_files(memory: CrowdMemory | None, warden: CrowdWarden | None) -> CrowdFiles | None:
    """The crowd files Ask loads as tables, when crowd watch is on."""
    if memory is None or warden is None:
        return None
    return CrowdFiles(
        readings=memory.path, assessments=warden.assessments_path, alerts=warden.alerts_path
    )


def build_crowd(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    perception: Perception,
    response: Response,
    runtime: RuntimeLoop | None,
) -> tuple[CrowdMemory | None, CrowdWarden | None, tuple[Callable[[], object], ...]]:
    """Crowd early warning: readings every few seconds and the warden that judges them.

    Only watched cameras (`POST /crowd/watch`) and `RULECAM_CROWD_CAMERAS` are measured; Ask's
    `crowd_scan` measures any camera on demand. SAM 3.1 (Meta hosted) counts on tiles; the ground
    calibration is the operator's marked floor points, else the active plan's, else
    MoGe-2's on the GPU pod, else the crowd's own people. The warden uses Muse Spark to assess,
    Muse Glimmer to check frames, the site's notifier, the evidence chain and MMS-TTS (when the
    `ml` extra is installed). Prototype mode only, and only with `RULECAM_CROWD_EVERY_S` > 0.

    Returns:
        The memory, the warden and their tickers; `(None, None, ())` when off (logged when Spark
        has no key), and `/crowd/*` then answers 503.
    """
    if not settings.rulecam_prototype_mode or settings.rulecam_crowd_every_s <= 0:
        return None, None, ()
    try:
        spark = SparkClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    except ClientConfigError as exc:
        _log.warning("crowd_disabled", error=str(exc))
        return None, None, ()
    glimmer = GlimmerClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
    crowd_dir = settings.rulecam_data_dir / "crowd"
    frames = FrameGrabber(perception.chunks, crowd_dir / "frames")
    geometry = geometry_estimator(settings, perception)
    model: ModelCalibration | None = None
    if geometry is not None:

        def model(camera_id: str, frame: Path) -> Calibration | None:
            return model_calibration(
                camera_id, frame, geometry=geometry, segmenter=perception.hosted
            )

    def planned() -> dict[str, Calibration]:
        plan = runtime.plan if runtime is not None else None
        found = plan.calibration if plan is not None else None
        return {found.camera_id: found} if found is not None else {}

    cameras = [c.strip() for c in settings.rulecam_crowd_cameras.split(",") if c.strip()]

    def named_zones(camera_id: str) -> list[CrowdZone]:
        return warden.zones(camera_id)  # the warden is made below; called only once ticking

    def counting(camera_id: str) -> tuple[str | None, int | None]:
        return warden.counting(camera_id)

    memory = CrowdMemory(
        frames=frames,
        scanner=CrowdScanner(
            segmenter=perception.hosted,
            out_dir=settings.rulecam_data_dir / "strips" / "crowd",
            concept=settings.rulecam_crowd_concept,
            grid=settings.rulecam_crowd_grid,
        ),
        calibrations=Calibrations(plan=planned, model=model, clock=clock.now),
        path=crowd_dir / "readings.jsonl",
        every_s=settings.rulecam_crowd_every_s,
        cameras=lambda: {*cameras, *warden.watched()},
        zones=named_zones,
        counting=counting,
        clock=clock,
    )
    names = camera_names(settings.rulecam_camera_names)
    warden = CrowdWarden(
        memory=memory,
        frames=frames,
        spark=spark,
        glimmer=glimmer,
        glimmer_model=glimmer.model,
        chain=response.chain,
        notifier=response.telegram or response.whatsapp or response.notifier,
        tts=crowd_tts(bus),
        clock=clock,
        out_dir=crowd_dir,
        every_s=settings.rulecam_crowd_assess_every_s,
        site_profile=settings.rulecam_site_profile,
        camera_names=names,
    )
    languages = [x for x in settings.rulecam_crowd_languages.split(",") if x.strip()]
    if settings.rulecam_crowd_instruction:
        for camera_id in cameras:
            warden.watch(camera_id, settings.rulecam_crowd_instruction, languages)
    return memory, warden, (memory.tick, warden.tick)


def crowd_tts(bus: Bus) -> TTS | None:
    """Meta MMS-TTS for the warden's announcements, when `transformers` is installed."""
    if importlib.util.find_spec("transformers") is None:
        _log.info("crowd_tts_off", reason="the ml extra (transformers) is not installed")
        return None
    return MmsTts(bus)


def cost_watch(
    settings: Settings,
    meter: CostMeter,
    runtime: RuntimeLoop | None,
    chunks: ChunkIndex,
    *,
    clock: Clock,
) -> CostWatch | None:
    """The cost ticker (dollar budget, cost per camera-hour, idle GPU stop); runtime only."""
    if runtime is None:
        return None
    return CostWatch(
        meter,
        clock=clock,
        plan=lambda: runtime.plan,
        last_seen=chunks.last_seen,
        idle_stop=IdleGpuStop.from_settings(settings),
        busy=lambda: runtime.verify_pending > 0,
    )


def false_alarm_regressions(
    plan: Callable[[], ActivePlan | None], hard_negatives: HardNegatives
) -> FalseAlarmSink:
    """A *False alarm* sink that records the tap as a permanent Rule CI regression (WS1f).

    Args:
        plan: The plan being enforced (its rules give the alert's concepts).
        hard_negatives: The regression recorder.

    Returns:
        The sink; an alert whose rule is not in the current plan is logged and skipped.
    """

    def record(event_id: str, rule_id: str | None) -> None:
        active = plan()
        rules = active.rules if active is not None else ()
        rule = next((r for r in rules if r.id == rule_id), None)
        if rule is None:
            _log.info("false_alarm_rule_unknown", event_id=event_id, rule_id=rule_id)
            return
        hard_negatives.from_false_alarm(rule, event_id)

    return record


def owner_alerts_router(
    response: Response, *, on_false_alarm: FalseAlarmSink | None = None
) -> APIRouter:
    """The alerts PWA routes (`/alerts`, `/ack`, interlock resume, `/drills`), owner-only.

    They show alert titles and clips and can restore power, so they need the owner (loopback or
    an owner session), like every other route that reads traces or changes state.
    """
    router = response.router(dependencies=_OWNER, on_false_alarm=on_false_alarm)
    runner = DrillRunner(response.escalation, response.chain, SystemClock())
    router.include_router(drills_router(runner), dependencies=_OWNER)
    return router


def viewer_origins(public_ui_url: str | None) -> frozenset[str]:
    """Return the origin of the public web UI (scheme://host[:port]) as the only viewer origin."""
    if not public_ui_url:
        return frozenset()
    parts = urlsplit(public_ui_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        msg = f"PUBLIC_UI_URL must be an http(s) URL, got {public_ui_url!r}"
        raise ValueError(msg)
    return frozenset({f"{parts.scheme}://{parts.netloc}"})


def openapi_json(app: FastAPI) -> str:
    """Return the app's OpenAPI schema as pretty JSON (input to `openapi-typescript`)."""
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


app = create_app(services_from_settings(get_settings()))
