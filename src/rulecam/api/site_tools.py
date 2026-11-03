"""The festival and supermarket tools: `/context`, `/crowd` and `/shelf` (all owner-only).

- `/context`: Muse Spark 1.3 with `web_search`, every source checked against the pages the
  search returned (`rulecam.context`);
- `/crowd`: SAM 3.1 person-track density, each episode watched on video by Muse Spark
  (`rulecam.crowd`);
- `/shelf`: Muse Spark finds shelf gaps on an image, SAM 3.1 confirms no product is in them
  (`rulecam.shelf`).

Without a Spark key `/context` and `/shelf` answer 503; `/crowd` still reports density, with the
video checks off and saying so.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter

from rulecam.ask.vision import FrameGrabber
from rulecam.ask.watch import ClipJoiner
from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.config import Settings
from rulecam.context import SiteContextService, SiteLocation, context_router
from rulecam.contracts import Clock
from rulecam.crowd.api import crowd_scan_router
from rulecam.crowd.density import CrowdLevels
from rulecam.crowd.watch import CrowdWatch
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.factory import Perception
from rulecam.shelf import ShelfScanner, shelf_router

_log = get_logger(__name__)


def site_location(settings: Settings) -> SiteLocation:
    """The site's approximate location from `RULECAM_SITE_CITY`, `_REGION`, `_COUNTRY`."""
    return SiteLocation(
        city=settings.rulecam_site_city,
        region=settings.rulecam_site_region,
        country=settings.rulecam_site_country.upper(),
        timezone="" if settings.rulecam_site_timezone == "UTC" else settings.rulecam_site_timezone,
    )


def build_site_tools(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    perception: Perception,
    site_name: str,
    dependencies: Sequence[Any] = (),
) -> tuple[APIRouter, ...]:
    """The `/context`, `/crowd` and `/shelf` routers."""
    data = settings.rulecam_data_dir
    try:
        spark: SparkClient | None = SparkClient.from_settings(
            settings, clock=clock, bus=bus, cache=cache
        )
    except ClientConfigError as exc:
        _log.warning("site_tools_without_spark", error=str(exc))
        spark = None
    joiner = ClipJoiner(
        perception.chunks, data / "crowd" / "clips", max_seconds=settings.rulecam_ask_max_watch_s
    )
    crowd = CrowdWatch(
        tracks_root=data / "tracks",
        spark=spark,
        files=spark,
        joiner=joiner,
        levels=CrowdLevels(
            busy=settings.rulecam_crowd_busy_per_m2,
            crowded=settings.rulecam_crowd_crowded_per_m2,
            critical=settings.rulecam_crowd_critical_per_m2,
        ),
    )
    context = (
        SiteContextService(
            spark,
            clock=clock,
            out_dir=data / "context",
            site_name=site_name,
            location=site_location(settings),
        )
        if spark is not None
        else None
    )
    shelf = (
        ShelfScanner(
            spark=spark,
            files=spark,
            sam=perception.hosted,
            out_dir=data / "shelf" / "marked",
            frames=FrameGrabber(perception.chunks, data / "shelf" / "frames"),
        )
        if spark is not None
        else None
    )
    return (
        context_router(context, dependencies=dependencies),
        crowd_scan_router(crowd, dependencies=dependencies),
        shelf_router(
            shelf,
            uploads_dir=data / "shelf" / "uploads",
            marked_dir=data / "shelf" / "marked",
            dependencies=dependencies,
        ),
    )
