"""Proof portal routes: the owner makes and shares packs; anyone with a link reads one.

Owner-only (the `dependencies` passed in, normally `require_owner`):

- `POST /proof/packs` starts a pack (`PackRequest`) and returns its entry with `status:
  building`; the numbers take a second, Muse Spark's clauses and summaries up to a few minutes, so
  the build runs on a worker thread and `GET /proof/packs/{id}` reports `ready` or `failed`.
- `GET /proof/packs` lists packs; `GET /proof/packs/{id}` returns one with its body and hash.
- `GET /proof/packs/{id}/document?format=pdf|html` prints it (PDF through Playwright; the
  printable HTML when Chromium is missing, as incident reports do).
- `POST /proof/packs/{id}/share` mints a signed, expiring link (`ShareRequest.hours`) with its
  QR code; `POST /proof/packs/{id}/revoke` stops every link to the pack.

Public (no login; the token is the permission):

- `GET /proof/shared/{token}` returns the pack, its hash and the link's expiry; 403 for a forged
  token, 410 for an expired or revoked one or a pack that changed since the link was minted.
- `GET /proof/shared/{token}/document?format=pdf|html` prints it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, params
from fastapi import Path as PathParam
from fastapi.responses import Response as HttpResponse

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.compliance.build import BuildInputs, build_pack
from rulecam.compliance.models import (
    PackEntry,
    PackRequest,
    SharedPack,
    ShareLink,
    ShareRequest,
)
from rulecam.compliance.proofs import pack_hash
from rulecam.compliance.render import render_html
from rulecam.compliance.rules import ClauseFinder
from rulecam.compliance.share import PackStore, ShareError, ShareSigner, load_secret, now_utc
from rulecam.config import Settings
from rulecam.contracts import Clock, PrivacyMode
from rulecam.evidence.incident import PdfPrinter, report_document
from rulecam.evidence.packs import qr_data_uri, render_pdf
from rulecam.log import get_logger
from rulecam.planner.pipeline import PlanStore
from rulecam.planner.standards import StandardsLibrary
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

PackIdPath = Annotated[str, PathParam(pattern=r"^pp-[0-9a-f]{12}$")]
TokenPath = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_\-]{8,400}\.[A-Za-z0-9_\-]{20,100}$")]
VIEW_PATH = "/inspector/view"


class ProofService:
    """Builds, stores and shares packs."""

    def __init__(
        self,
        inputs: Callable[[], BuildInputs],
        store: PackStore,
        signer: ShareSigner,
        *,
        public_ui_url: str | None = None,
        executor: Executor | None = None,
        printer: PdfPrinter = render_pdf,
    ) -> None:
        """Create the service.

        Args:
            inputs: Builds the inputs at request time (the privacy mode can change).
            store: Where packs live.
            signer: Mints and checks share tokens.
            public_ui_url: The web UI's public origin for share links.
            executor: Runs builds off the request (one worker thread by default).
            printer: Prints HTML to PDF.
        """
        self._inputs = inputs
        self.store = store
        self._signer = signer
        self._public_ui_url = public_ui_url.rstrip("/") if public_ui_url else None
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="proof")
        self._printer = printer

    def start(self, request: PackRequest) -> PackEntry:
        """Reserve a pack id, store `building` and build in the background."""
        inputs = self._inputs()
        pack_id = inputs.new_id()
        entry = PackEntry(
            pack_id=pack_id,
            status="building",
            created_at=now_utc(),
            site=inputs.site,
            title=request.title or "Proof pack",
        )
        self.store.save(entry)
        self._executor.submit(self._build, request, inputs, entry)
        return entry

    def _build(self, request: PackRequest, inputs: BuildInputs, entry: PackEntry) -> None:
        try:
            pack = build_pack(request, inputs, pack_id=entry.pack_id)
        except Exception as exc:  # stored for the owner to see; nothing half-built is served
            _log.exception("proof_pack_failed", pack_id=entry.pack_id)
            self.store.save(entry.model_copy(update={"status": "failed", "error": str(exc)}))
            return
        self.store.save(
            entry.model_copy(
                update={
                    "status": "ready",
                    "title": pack.title,
                    "pack": pack,
                    "pack_hash": pack_hash(pack.model_dump(mode="json")),
                }
            )
        )
        _log.info("proof_pack_ready", pack_id=entry.pack_id, records=len(pack.records))

    def ready(self, pack_id: str) -> PackEntry:
        """A stored pack that finished building.

        Raises:
            HTTPException: 404 when unknown, 409 while building or after a failure.
        """
        entry = self.store.get(pack_id)
        if entry is None:
            raise HTTPException(404, "no pack with this id")
        if entry.status != "ready" or entry.pack is None or entry.pack_hash is None:
            raise HTTPException(409, f"the pack is {entry.status}")
        return entry

    def share(self, pack_id: str, request: ShareRequest) -> ShareLink:
        """A signed link to a ready pack, valid for `request.hours`."""
        entry = self.ready(pack_id)
        if pack_id in self.store.revoked():
            raise HTTPException(409, "links to this pack were revoked; make a new pack")
        expires = now_utc() + timedelta(hours=request.hours)
        token = self._signer.mint(pack_id, str(entry.pack_hash), expires)
        origin = self._public_ui_url or request.ui_origin or ""
        url = f"{origin}{VIEW_PATH}?t={token}"
        return ShareLink(
            pack_id=pack_id,
            pack_hash=str(entry.pack_hash),
            token=token,
            url=url,
            expires_at=expires,
            qr=qr_data_uri(url),
        )

    def shared(self, token: str) -> tuple[SharedPack, str]:
        """The pack a token opens, and the link URL for its printout.

        Raises:
            HTTPException: 403 for a forged token; 410 when expired, revoked or changed.
        """
        try:
            claims = self._signer.check(token, now_utc())
        except ShareError as exc:
            status = 410 if "expired" in str(exc) else 403
            raise HTTPException(status, str(exc)) from exc
        entry = self.store.get(claims.p)
        if entry is None or entry.pack is None or claims.p in self.store.revoked():
            raise HTTPException(410, "this link was revoked")
        if entry.pack_hash != claims.h:
            raise HTTPException(410, "the pack changed after this link was made")
        expires = datetime.fromtimestamp(claims.x, tz=UTC)
        url = f"{self._public_ui_url or ''}{VIEW_PATH}?t={token}"
        return SharedPack(pack=entry.pack, pack_hash=claims.h, expires_at=expires), url

    def document(
        self, entry: PackEntry, url: str | None, fmt: Literal["pdf", "html"]
    ) -> HttpResponse:
        """The pack as PDF (or printable HTML) for download (`entry` from `ready`)."""
        if entry.pack is None or entry.pack_hash is None:
            raise HTTPException(409, f"the pack is {entry.status}")
        html = render_html(entry.pack, entry.pack_hash, url)
        if fmt == "pdf":
            body, media_type = report_document(html, self._printer)
        else:
            body, media_type = html.encode("utf-8"), "text/html"
        extension = "pdf" if media_type == "application/pdf" else "html"
        return HttpResponse(
            body,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{entry.pack_id}.{extension}"',
                "Cache-Control": "no-store",
                "X-Rulecam-Report-Format": extension,
            },
        )


def _owner_routes(router: APIRouter, service: ProofService, owner: list[params.Depends]) -> None:

    @router.post("/packs", dependencies=owner, status_code=202)
    def create(request: PackRequest) -> PackEntry:
        if request.end <= request.start:
            raise HTTPException(422, "the period must end after it starts")
        return service.start(request)

    @router.get("/packs", dependencies=owner)
    def packs() -> list[PackEntry]:
        return service.store.list()

    @router.get("/packs/{pack_id}", dependencies=owner)
    def pack(pack_id: PackIdPath) -> PackEntry:
        entry = service.store.get(pack_id)
        if entry is None:
            raise HTTPException(404, "no pack with this id")
        return entry

    @router.get("/packs/{pack_id}/document", dependencies=owner, response_class=HttpResponse)
    def document(pack_id: PackIdPath, format: Literal["pdf", "html"] = "pdf") -> HttpResponse:
        return service.document(service.ready(pack_id), None, format)

    @router.post("/packs/{pack_id}/share", dependencies=owner)
    def share(pack_id: PackIdPath, request: ShareRequest) -> ShareLink:
        return service.share(pack_id, request)

    @router.post("/packs/{pack_id}/revoke", dependencies=owner)
    def revoke(pack_id: PackIdPath) -> dict[str, bool]:
        service.ready(pack_id)
        service.store.revoke(pack_id)
        return {"revoked": True}


def _public_routes(router: APIRouter, service: ProofService) -> None:
    @router.get("/shared/{token}")
    def shared(token: TokenPath) -> SharedPack:
        return service.shared(token)[0]

    @router.get("/shared/{token}/document", response_class=HttpResponse)
    def shared_document(token: TokenPath, format: Literal["pdf", "html"] = "pdf") -> HttpResponse:
        body, url = service.shared(token)
        entry = service.ready(body.pack.pack_id)
        return service.document(entry, url, format)


def proof_router(
    service: ProofService, *, dependencies: Sequence[params.Depends] = ()
) -> APIRouter:
    """The `/proof` routes; `dependencies` guard the owner routes only."""
    router = APIRouter(prefix="/proof", tags=["proof"])
    _owner_routes(router, service, list(dependencies))
    _public_routes(router, service)
    return router


def build_proof_router(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    chain_root: Path,
    site: str,
    privacy_mode: Callable[[], PrivacyMode | None],
    dependencies: Sequence[params.Depends] = (),
    plans_dir: Path = Path("plans"),
) -> APIRouter:
    """The `/proof` routes wired from settings (`services_from_settings` mounts them).

    Muse Spark 1.3 (through the replay cache) writes the summaries and grounds each rule's
    clause; without a Meta key the packs still build, with no summaries and no new clauses.
    The share secret is `RULECAM_SHARE_SECRET`, else a key kept in `<data>/compliance/share.key`.
    """
    root = settings.rulecam_data_dir / "compliance"
    try:
        spark: SparkClient | None = SparkClient.from_settings(
            settings, clock=clock, bus=bus, cache=cache
        )
    except ClientConfigError as exc:
        _log.warning("proof_spark_disabled", error=str(exc))
        spark = None
    clauses = ClauseFinder.live(
        spark, StandardsLibrary.from_settings(settings), root / "clauses.json"
    )
    secret = settings.rulecam_share_secret
    signer = ShareSigner(
        lambda: load_secret(secret.get_secret_value() if secret else None, root / "share.key")
    )

    def inputs() -> BuildInputs:
        return BuildInputs(
            chain_root=chain_root,
            site=site,
            plans=PlanStore(plans_dir),
            roots_dir=Path("evidence/roots"),
            privacy_mode=privacy_mode() or PrivacyMode.STRICT,
            stills_root=settings.rulecam_data_dir / "strips",
            spark=spark,
            clauses=clauses,
            timezone=settings.rulecam_site_timezone,
            clock=clock.now,
        )

    service = ProofService(inputs, PackStore(root), signer, public_ui_url=settings.public_ui_url)
    return proof_router(service, dependencies=dependencies)
