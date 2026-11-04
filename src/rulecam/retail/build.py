"""Wiring the supermarket service into the API (`build_retail`, called from `api.app`)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from fastapi import APIRouter

from rulecam.advisor.api import AdviceStore
from rulecam.ask import AskAgent
from rulecam.ask.agent import camera_names
from rulecam.ask.vision import FrameGrabber
from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted import SparkClient, VoiceTranscribeClient
from rulecam.clients.open_weights.glimmer import GlimmerClient
from rulecam.config import Settings
from rulecam.contracts import Clock, Notifier
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache
from rulecam.retail.advice import AdviceWriter
from rulecam.retail.api import Transcribe, retail_router
from rulecam.retail.brief import BriefWriter
from rulecam.retail.checks import HazardChecker, QueueChecker, ShelfChecker, Site
from rulecam.retail.exits import ExitChecker
from rulecam.retail.models import CheckKind
from rulecam.retail.proof import ProofChecker
from rulecam.retail.service import CardBook, Checker, ImageChecker, RetailService, image_resolver
from rulecam.retail.staff import StaffDesk
from rulecam.retail.store import FindingLog, TaskBoard, WatchBook
from rulecam.runtime.factory import Perception
from rulecam.runtime.response import Response
from rulecam.shelf import ShelfScanner

_log = get_logger(__name__)


def outbound(response: Response) -> Notifier:
    """The site's outbound notifier: Telegram, else WhatsApp, else the LAN alerts app."""
    return response.telegram or response.whatsapp or response.notifier


def build_retail(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    perception: Perception,
    response: Response,
    chain: EventChain,
    advice: AdviceStore,
    ask: AskAgent | None,
    dependencies: Sequence[Any] = (),
) -> tuple[APIRouter, tuple[Callable[[], object], ...]]:
    """The `/retail` router and the watch ticker.

    Without a Spark key the routes answer 503 and there is no ticker. Glimmer gives the hazard
    and restock-proof second looks when `GLIMMER_BASE_URL` is set; Voice Transcribe takes
    spoken questions when the Spark key is set.
    """
    data = settings.rulecam_data_dir
    root = data / "retail"
    frames = FrameGrabber(perception.chunks, root / "frames")
    marks = root / "marked"
    uploads = root / "uploads"
    shelf_marked = data / "shelf" / "marked"
    resolve = image_resolver([marks, frames.out_dir, uploads, shelf_marked])
    try:
        spark = SparkClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    except ClientConfigError as exc:
        _log.warning("retail_without_spark", error=str(exc))
        return retail_router(
            None, uploads_dir=uploads, files=resolve, dependencies=dependencies
        ), ()
    glimmer = (
        GlimmerClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
        if settings.glimmer_base_url
        else None
    )
    glimmer_model = glimmer.model if glimmer is not None else ""
    names = camera_names(settings.rulecam_camera_names)
    site = Site(settings.rulecam_site_profile, names)
    findings = FindingLog(root / "findings.jsonl")
    tasks = TaskBoard(root / "tasks.json")

    queue = QueueChecker(
        spark=spark,
        files=spark,
        sam=perception.hosted,
        frames=frames,
        site=site,
        clock=clock,
        marks_dir=marks,
        history=lambda cam, kind: [
            f for f in findings.recent(limit=50, kind=kind) if f.camera_id == cam
        ],
    )
    hazard = HazardChecker(
        spark=spark,
        files=spark,
        sam=perception.hosted,
        glimmer=glimmer,
        glimmer_model=glimmer_model,
        frames=frames,
        site=site,
        clock=clock,
        marks_dir=marks,
    )
    scanner = ShelfScanner(
        spark=spark, files=spark, sam=perception.hosted, out_dir=shelf_marked, frames=frames
    )
    shelf = ShelfChecker(scanner, site, clock, glimmer=glimmer, glimmer_model=glimmer_model)
    exits = ExitChecker(
        spark=spark,
        files=spark,
        sam=perception.hosted,
        frames=frames,
        site=site,
        clock=clock,
        marks_dir=marks,
        history=lambda cam, kind: [
            f for f in findings.recent(limit=50, kind=kind) if f.camera_id == cam
        ],
    )
    checkers: dict[CheckKind, Checker] = {
        "queue": queue,
        "hazard": hazard,
        "shelf": shelf,
        "exit": exits,
    }
    image_checkers: dict[CheckKind, ImageChecker] = {"hazard": hazard, "shelf": shelf}
    desk = StaffDesk(
        notifier=outbound(response),
        alerts=response.notifier.get,
        tasks=tasks,
        chain=chain,
        clock=clock,
        image_path=resolve,
    )
    service = RetailService(
        checkers=checkers,
        image_checkers=image_checkers,
        frames=frames,
        findings=findings,
        watches=WatchBook(root / "watches.json"),
        tasks=tasks,
        desk=desk,
        cards=CardBook(root / "cards.json"),
        writer=AdviceWriter(
            spark=spark,
            runs=data / "ask" / "runs.jsonl",
            clock=clock,
            camera_names=names,
            site_profile=settings.rulecam_site_profile,
        ),
        briefs=BriefWriter(spark, clock),
        advice=advice,
        chain=chain,
        clock=clock,
        ask_runs=data / "ask" / "runs.jsonl",
        camera_names=names,
        site_profile=settings.rulecam_site_profile,
        proof=ProofChecker(spark=spark, files=spark, glimmer=glimmer, glimmer_model=glimmer_model),
        image_path=resolve,
    )
    transcribe: Transcribe | None = None
    try:
        voice = VoiceTranscribeClient.from_settings(settings, clock=clock, bus=bus, cache=cache)

        def transcribe_wav(wav: bytes, langs: list[str] | None) -> list[dict[str, str]]:
            return voice.transcribe(wav, lang_bias=langs)

        transcribe = transcribe_wav
    except ClientConfigError as exc:
        _log.warning("retail_without_voice", error=str(exc))
    router = retail_router(
        service,
        uploads_dir=uploads,
        files=resolve,
        ask=ask,
        transcribe=transcribe,
        dependencies=dependencies,
    )
    return router, (service.tick,)
