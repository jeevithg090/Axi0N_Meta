"""Public routes of the worker rules page (task P2-11), reachable through the tunnel.

- `GET /worker/{plan_id}/{camera}?lang=`: the page payload (`WorkerPage`); the path matches the
  rule cards' QR link (`cards.build.worker_url`).
- `GET /worker/{plan_id}/{camera}/poster?lang=`: the printable A4 poster (HTML).
- `POST /worker/dispute`: an anonymous tap dispute (`DisputeRequest` → `DisputeResult`).

Everything is read-only except the dispute. The dispute takes JSON only (a cross-site form
cannot send it without a CORS preflight, which the API never answers), at most 4 KiB, and is
bounded per client, per site and per event (`worker.disputes`). The client address is used only
for that in-memory limit; it is never logged or stored.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import HTMLResponse

from rulecam.contracts import Clock, SitePlan
from rulecam.evidence.chain import EventChain
from rulecam.worker.disputes import DisputeService
from rulecam.worker.errors import DisputeLimitError, UnknownEventError
from rulecam.worker.log import read_site_log
from rulecam.worker.models import DisputeRequest, DisputeResult, WorkerPage
from rulecam.worker.page import build_page, camera_rules
from rulecam.worker.poster import render_poster

PlanLookup = Callable[[str], SitePlan | None]

MAX_DISPUTE_BYTES: Final = 4096
POSTER_HEADERS: Final = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

PlanId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
CameraId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
Lang = Annotated[str | None, Query(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")]


def _small_body(content_length: Annotated[str | None, Header()] = None) -> None:
    if content_length is not None and (
        not content_length.isdigit() or int(content_length) > MAX_DISPUTE_BYTES
    ):
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "dispute too large")


def worker_router(
    *,
    plans: PlanLookup,
    chain: EventChain,
    disputes: DisputeService,
    clock: Clock,
    public_base_url: str | None = None,
) -> APIRouter:
    """Build the router.

    Args:
        plans: Finds a plan by id (`None` when unknown).
        chain: The site's evidence chain (events and disputes are read from it).
        disputes: Accepts disputes.
        clock: Time source (today's chain file).
        public_base_url: Public base URL of the worker page for the poster's QR code
            (`PUBLIC_UI_URL`); without it the poster answers 503.

    Returns:
        The `/worker` router.
    """
    router = APIRouter(prefix="/worker", tags=["worker"])

    def plan_for(plan_id: str, camera: str) -> SitePlan:
        plan = plans(plan_id)
        if plan is None or not camera_rules(plan, camera):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such camera in this plan")
        return plan

    @router.post("/dispute", dependencies=[Depends(_small_body)])
    def dispute(body: DisputeRequest, request: Request) -> DisputeResult:
        client = request.client.host if request.client else ""
        try:
            return disputes.submit(body, client)
        except DisputeLimitError as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
        except UnknownEventError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.get("/{plan_id}/{camera}")
    def page(plan_id: PlanId, camera: CameraId, lang: Lang = None) -> WorkerPage:
        plan = plan_for(plan_id, camera)
        log = read_site_log(chain, clock.now().date(), plan.retention_days)
        return build_page(plan, camera, lang, log)

    @router.get("/{plan_id}/{camera}/poster", response_class=HTMLResponse)
    def poster(plan_id: PlanId, camera: CameraId, lang: Lang = None) -> HTMLResponse:
        plan = plan_for(plan_id, camera)
        if public_base_url is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "set PUBLIC_UI_URL to print the poster"
            )
        html = render_poster(plan, camera, lang, public_base_url)
        return HTMLResponse(html, headers=POSTER_HEADERS)

    return router
