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

Response (WS1b): `services_from_settings` builds the policy, firewall and actions once
(`runtime.response`), mounts the alerts PWA routes (`/alerts`, `/ack`, `/interlock/{device}/resume`,
owner-only), ticks the escalation timers once a second, and serves how the response side is
configured (Tier 2, firewall, dry run, interlock) in `GET /runtime/metrics` under `response`.
Upheld disputes and owner corrections become permanent Rule CI regressions (WS1f).

`make api` serves the module-level `app`, built from settings with the replay cache, the bus, the
evidence chain, perception and Pair-a-Phone; `RULECAM_RUNTIME=true` also starts the runtime loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, WebSocket, params, status
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from rulecam.api.auth import ClientAddress, OwnerAuth
from rulecam.api.plans import PlannerRunner, PlanRegistry, QuestionsService, plans_router
from rulecam.api.prometheus import (
    HealthProbe,
    MetricsSources,
    VerdictCounter,
    dependency_probes,
    metrics_router,
)
from rulecam.api.ws import mirror_bus
from rulecam.bus import Bus, Topic
from rulecam.capture.pair_phone import PairPhone, build_router, require_owner
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted import SparkClient, VoiceTranscribeClient
from rulecam.clients.open_weights.glimmer import GlimmerClient
from rulecam.clients.open_weights.spark_open import SparkOpenClient
from rulecam.clock import SystemClock
from rulecam.config import Settings, get_settings
from rulecam.contracts import Calibration, Chunk, Clock, SitePlan
from rulecam.evidence.chain import ChainReport, EventChain, read_chain, verify_chain
from rulecam.evidence.merkle import chain_root, read_day_roots
from rulecam.log import get_logger
from rulecam.perception.correction import CorrectionService
from rulecam.perception.correction_api import correction_router
from rulecam.perception.metrics import PerceptionMetrics, PerceptionMetricsReport
from rulecam.perception.router import Purpose
from rulecam.planner.errors import PipelineError
from rulecam.planner.pipeline import PlannerPipeline, PlanStore
from rulecam.planner.runner import ChunkFrames, PlannerService
from rulecam.planner.site_config import SiteConfigError, load_site_config
from rulecam.planner.standards import StandardsLibrary
from rulecam.planner.steps.audit import audit_hook
from rulecam.replay.cache import Mode, ModeChanged, ReplayCache, badge_for
from rulecam.rule_ci import prove_plan_hook
from rulecam.rule_ci.hard_negatives import HardNegatives
from rulecam.runtime.activate import PlanActivator, restore_activation
from rulecam.runtime.factory import (
    ChainEventSource,
    ChunkIndex,
    Perception,
    build_perception,
    build_runtime,
    build_tier1,
)
from rulecam.runtime.loop import ActivePlan, LoopMetrics, RuntimeLoop, plan_concepts
from rulecam.runtime.response import Response, ResponseStatus, build_response
from rulecam.verify.tier2 import SparkOpenVerifier
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
        uploads_dir: Where planner uploads are stored (`POST /plans/uploads`).
        planner: The Site Planner runner (P2-06); `POST /plans/requests` needs it.
        activator: Activates approved plans in the runtime; approval answers 503 without one.
        viewer_origins: Browser origins (e.g. the public web UI) that may watch the read-only
            `/ws` mirror without being the owner, on a public demo only. Nothing that changes
            state accepts them without an owner session.
        auth: Owner login (`rulecam.api.auth`); `None` means loopback only, with login off.
        questions: The planner's questions and re-runs (`PlannerService`).
        verdicts: Verdict counts for `GET /metrics`, fed from `verdict.new`.
        health_probes: Dependencies (Glimmer, the SAM pod) whose health `GET /metrics` shows.
        response: How the policy, firewall and actions are configured (`/runtime/metrics`).
        public_demo: Demo sites only: non-owners may watch `/ws` (from a viewer origin) and read
            plans. Off by default; a real site keeps those reads owner-only.
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
    uploads_dir: Path | None = None
    planner: PlannerRunner | None = None
    activator: PlanActivator | None = None
    viewer_origins: frozenset[str] = frozenset()
    auth: OwnerAuth | None = None
    questions: QuestionsService | None = None
    verdicts: VerdictCounter = field(default_factory=VerdictCounter)
    health_probes: Sequence[HealthProbe] = ()
    response: ResponseStatus | None = None
    public_demo: bool = False


class RuntimeReport(LoopMetrics):
    """Body of `GET /runtime/metrics`: the loop's metrics plus the response side's status."""

    response: ResponseStatus | None = None


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


VERIFY_PAGE_DIR = Path(__file__).resolve().parent.parent / "evidence" / "verify_page"


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
    """Body of `GET /health`."""

    ok: bool
    site: str
    mode: Mode
    runtime: bool


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
        return RuntimeReport(**dict(runtime().metrics), response=services.response)

    return router


def _metrics_sources(services: AppServices) -> MetricsSources:
    return MetricsSources(
        runtime=lambda: services.runtime.metrics if services.runtime is not None else None,
        perception=services.perception_metrics.report,
        verdicts=services.verdicts,
        probes=services.health_probes,
    )


def _routers(services: AppServices, auth: OwnerAuth) -> list[APIRouter]:
    routers = [
        auth.router(),
        _core_router(services, auth),
        metrics_router(_metrics_sources(services)),
        _evidence_router(services),
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
    )
    frames = ChunkFrames(
        perception.chunks,
        clock=clock,
        out_dir=settings.rulecam_data_dir / "planner_frames",
        window_s=settings.planner_survey_window_s,
    )
    return PlannerService(pipeline, site=site_config, frames=frames, on_plan=plans.put, clock=clock)


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
    response = build_response(
        settings,
        tier1=build_tier1(settings, cache=cache, clock=clock, bus=bus),
        segmenter=perception.hosted,
        cache=cache,
        clock=clock,
        bus=bus,
        chain=chain,
    )
    runtime = (
        build_runtime(
            settings, perception, cache=cache, clock=clock, bus=bus, chain=chain, response=response
        )
        if settings.rulecam_runtime
        else None
    )
    hard_negatives = HardNegatives(settings.regressions_dir)

    def concepts() -> list[str]:
        plan = runtime.plan if runtime is not None else None
        return plan_concepts(plan) if plan is not None else []

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
    if runtime is not None:
        restore_activation(runtime, active_plan_path)
    activator = PlanActivator(
        runtime=runtime,
        chain=chain,
        state_path=active_plan_path,
        calibrations=run_calibrations(PlanStore(PLANS_DIR)),
    )
    plans = PlanRegistry()
    planner = build_planner(
        settings, cache=cache, clock=clock, bus=bus, perception=perception, plans=plans, site=site
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
        auth=auth,
        health_probes=dependency_probes(settings.glimmer_base_url, settings.sam_base_url),
        tickers=response.tickers,
        response=response.status,
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
            owner_alerts_router(response),
        ),
    )


def owner_alerts_router(response: Response) -> APIRouter:
    """The alerts PWA routes (`/alerts`, `/ack`, interlock resume), owner-only (P3-06).

    They show alert titles and clips and can restore power, so they need the owner (loopback or
    an owner session), like every other route that reads traces or changes state.
    """
    return response.router(dependencies=_OWNER)


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
