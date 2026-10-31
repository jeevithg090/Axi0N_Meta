"""The Rulecam API (task P3-08): one FastAPI app for the UI, phones and the verify page.

`create_app(services)` builds the app from injected services, so tests and the demo wire exactly
what they need. It mounts `/pair` (P1-09) and `/correction` (P1-10) when those services are set,
plus `/plans`, `/cameras`, `/metrics/perception` and the static `/verify` page. Routers owned by
tasks still in flight (`/worker`, `/ack`, `/interlock`, `/advice`, `/surprise`) are passed in
through `AppServices.routers` as they land.

Anything that changes state (`PUT /mode`, `PUT /runtime/plan`, plan create/approve) and the `/ws`
mirror are owner-only: both the target host and the client must be loopback, because the same app
is reachable through the Pair-a-Phone tunnel.

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

from fastapi import APIRouter, Depends, FastAPI, HTTPException, WebSocket, status
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from rulecam.api.plans import PlanRegistry, plans_router
from rulecam.api.ws import mirror_bus
from rulecam.bus import Bus, Topic
from rulecam.capture.pair_phone import PairPhone, build_router, require_owner
from rulecam.clock import SystemClock
from rulecam.config import Settings, get_settings
from rulecam.contracts import Chunk
from rulecam.evidence.chain import ChainReport, EventChain, verify_chain
from rulecam.log import get_logger
from rulecam.perception.correction import CorrectionService
from rulecam.perception.correction_api import correction_router
from rulecam.perception.metrics import PerceptionMetrics, PerceptionMetricsReport
from rulecam.perception.router import Purpose
from rulecam.replay.cache import Mode, ModeChanged, ReplayCache, badge_for
from rulecam.runtime.factory import ChainEventSource, ChunkIndex, build_perception, build_runtime
from rulecam.runtime.loop import ActivePlan, LoopMetrics, RuntimeLoop, plan_concepts

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
        verify_dir: Directory served at `/verify` (created if missing); `None` skips it.
        tickers: Called once a second off the event loop (e.g. `EscalationManager.tick`).
    """

    bus: Bus
    cache: ReplayCache
    chain_root: Path
    site: str
    runtime: RuntimeLoop | None = None
    routers: Sequence[APIRouter] = field(default_factory=tuple)
    perception_metrics: PerceptionMetrics = field(default_factory=PerceptionMetrics)
    pair: PairPhone | None = None
    correction: CorrectionService | None = None
    chunks: ChunkIndex | None = None
    plans: PlanRegistry = field(default_factory=PlanRegistry)
    verify_dir: Path | None = None
    tickers: Sequence[Callable[[], object]] = field(default_factory=tuple)


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


def _core_router(services: AppServices) -> APIRouter:
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
        try:
            require_owner(websocket)
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

    @router.get("/{day}/chain", response_class=PlainTextResponse)
    def chain(day: date) -> PlainTextResponse:
        text = _chain_file(services, day).read_text(encoding="utf-8")
        return PlainTextResponse(text, media_type="application/x-ndjson")

    @router.get("/{day}/verify")
    def verify(day: date) -> ChainReport:
        return verify_chain(_chain_file(services, day))

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
    def metrics() -> LoopMetrics:
        return runtime().metrics

    return router


def _routers(services: AppServices) -> list[APIRouter]:
    routers = [
        _core_router(services),
        _evidence_router(services),
        _runtime_router(services),
        plans_router(services.plans, services.bus),
    ]
    if services.pair is not None:
        routers.append(build_router(services.pair))
    if services.correction is not None:
        routers.append(correction_router(services.correction, services.bus))
    return [*routers, *services.routers]


def _lifespan(
    services: AppServices,
) -> Callable[[FastAPI], contextlib.AbstractAsyncContextManager[None]]:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        services.bus.bind_loop(asyncio.get_running_loop())
        tasks: list[asyncio.Task[None]] = []
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
        services: The injected bus, cache, chain location, runtime and extra routers.

    Returns:
        The FastAPI app. On start-up it binds the bus to the event loop (so worker threads can
        publish) and starts the runtime loop if there is one; on shutdown it stops it.
    """
    app = FastAPI(title="Rulecam", version="0.1.0", lifespan=_lifespan(services))
    for router in _routers(services):
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


def services_from_settings(settings: Settings, site: str = "demo") -> AppServices:
    """Build the default services from settings.

    Always: bus, replay cache, evidence chain, perception (SAM router, metrics, track store),
    Pair-a-Phone and the correction loop. With `RULECAM_RUNTIME=true`, also the runtime loop.

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
    runtime = (
        build_runtime(settings, perception, cache=cache, clock=clock, bus=bus, chain=chain)
        if settings.rulecam_runtime
        else None
    )

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
        verify_dir=settings.rulecam_data_dir / "verify",
    )


def openapi_json(app: FastAPI) -> str:
    """Return the app's OpenAPI schema as pretty JSON (input to `openapi-typescript`)."""
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


app = create_app(services_from_settings(get_settings()))
