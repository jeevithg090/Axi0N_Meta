"""The Rulecam API (task P3-08): one FastAPI app for the UI, phones and the verify page.

`create_app(services)` builds the app from injected services, so tests and the demo wire exactly
what they need. Routers owned by other tasks (`/pair` P1-09, `/correction` P1-10, `/plans`,
`/worker`, `/ack`, `/interlock`, `/advice`, `/surprise`) are passed in through
`AppServices.routers` as they land.

`make api` serves the module-level `app`, built from settings with the replay cache, the bus and
the evidence chain; the runtime loop starts when `AppServices.runtime` is set.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, WebSocket
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from rulecam.api.ws import mirror_bus
from rulecam.bus import Bus
from rulecam.clock import SystemClock
from rulecam.config import Settings, get_settings
from rulecam.evidence.chain import ChainReport, verify_chain
from rulecam.log import get_logger
from rulecam.replay.cache import Mode, ModeChanged, ReplayCache, badge_for
from rulecam.runtime.loop import ActivePlan, LoopMetrics, RuntimeLoop

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
        tickers: Called once a second off the event loop (e.g. `EscalationManager.tick`).
    """

    bus: Bus
    cache: ReplayCache
    chain_root: Path
    site: str
    runtime: RuntimeLoop | None = None
    routers: Sequence[APIRouter] = field(default_factory=tuple)
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

    @router.put("/mode")
    def set_mode(body: ModeRequest) -> ModeChanged:
        services.cache.set_mode(body.mode)
        return ModeChanged(mode=body.mode, badge=badge_for(body.mode))

    @router.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await mirror_bus(websocket, services.bus)

    return router


def _evidence_router(services: AppServices) -> APIRouter:
    router = APIRouter(prefix="/evidence", tags=["evidence"])

    @router.get("/days")
    def days() -> list[date]:
        folder = services.chain_root / services.site
        files = sorted(folder.glob("*.jsonl")) if folder.is_dir() else []
        return [date.fromisoformat(p.stem) for p in files]

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

    @router.put("/plan")
    def set_plan(plan: ActivePlan) -> dict[str, int]:
        runtime().set_plan(plan)
        return {"rules": len(plan.rules)}

    @router.get("/metrics")
    def metrics() -> LoopMetrics:
        return runtime().metrics

    return router


def create_app(services: AppServices) -> FastAPI:
    """Build the API around `services`.

    Args:
        services: The injected bus, cache, chain location, runtime and extra routers.

    Returns:
        The FastAPI app. On start-up it binds the bus to the event loop (so worker threads can
        publish) and starts the runtime loop if there is one; on shutdown it stops it.
    """

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        services.bus.bind_loop(asyncio.get_running_loop())
        tasks = [asyncio.create_task(_tick_forever(services.tickers))]
        if services.runtime is not None:
            tasks.append(asyncio.create_task(services.runtime.run()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            services.bus.bind_loop(None)

    app = FastAPI(title="Rulecam", version="0.1.0", lifespan=lifespan)
    for router in (
        _core_router(services),
        _evidence_router(services),
        _runtime_router(services),
        *services.routers,
    ):
        app.include_router(router)
    return app


def services_from_settings(settings: Settings, site: str = "demo") -> AppServices:
    """Build the default services: bus, replay cache and evidence chain, no runtime loop.

    Args:
        settings: Application settings.
        site: Site name for the chain folder.

    Returns:
        The services.
    """
    bus = Bus()
    cache = ReplayCache.from_settings(settings, clock=SystemClock(), bus=bus)
    return AppServices(
        bus=bus,
        cache=cache,
        chain_root=settings.rulecam_data_dir / "evidence" / "chain",
        site=site,
    )


def openapi_json(app: FastAPI) -> str:
    """Return the app's OpenAPI schema as pretty JSON (input to `openapi-typescript`)."""
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


app = create_app(services_from_settings(get_settings()))
