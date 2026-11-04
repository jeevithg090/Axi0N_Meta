"""Connect any camera in 30 seconds: RTSP/ONVIF CCTV, HLS, MJPEG, public streams, looped clips.

Every source becomes the same rolling H.264 chunks the capture `Chunker` and Pair-a-Phone make,
published on `chunk.ready`, so the runtime, the chunk index, Ask and the planner see a connected
CCTV camera exactly like any other. See `docs/usecases/cameras.md`.
"""

from __future__ import annotations

from pathlib import Path

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.config import Settings
from rulecam.connectors.api import connectors_router
from rulecam.connectors.errors import ConnectorError, SourceError, UnknownCameraError
from rulecam.connectors.manager import (
    AddCamera,
    CameraView,
    ConnectorManager,
    ConnectorSettings,
    LatestChunks,
    clip_roots,
)
from rulecam.connectors.sources import SourceKind, classify, redact
from rulecam.connectors.stream import StreamConfig, StreamHealth, StreamWorker
from rulecam.connectors.survey import CameraSurvey, CameraSurveyor, ImageSegmenter
from rulecam.contracts import Clock
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

__all__ = [
    "AddCamera",
    "CameraSurvey",
    "CameraSurveyor",
    "CameraView",
    "ConnectorError",
    "ConnectorManager",
    "ConnectorSettings",
    "SourceError",
    "SourceKind",
    "StreamConfig",
    "StreamHealth",
    "StreamWorker",
    "UnknownCameraError",
    "build_connectors",
    "classify",
    "connectors_router",
    "redact",
]


def build_connectors(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    sam: ImageSegmenter,
    chunks: LatestChunks | None,
    extra_clip_roots: tuple[Path, ...] = (),
) -> ConnectorManager:
    """The connector manager from settings: Spark and SAM for the first look when keyed.

    Args:
        settings: Application settings (`rulecam_data_dir`, `rulecam_connectors_*`).
        cache: The replay cache Spark calls go through.
        clock: Timestamps.
        bus: Where chunks are published.
        sam: SAM 3.1 on one image (the hosted client).
        chunks: The chunk index (snapshots and surveys of phones and other cameras).
        extra_clip_roots: More folders clip paths may point into.

    Returns:
        The manager (its router stops every camera when the app shuts down); without a Spark
        key, surveys answer `unavailable` and cameras still run.
    """
    root = settings.rulecam_data_dir / "connectors"
    try:
        spark: SparkClient | None = SparkClient.from_settings(
            settings, clock=clock, bus=bus, cache=cache
        )
    except ClientConfigError as exc:
        _log.warning("connectors_without_spark", error=str(exc))
        spark = None
    surveyor = (
        CameraSurveyor(spark=spark, files=spark, sam=sam, clock=clock, out_dir=root / "surveys")
        if spark is not None
        else None
    )
    return ConnectorManager(
        ConnectorSettings(
            root=root,
            fps=settings.rulecam_connectors_fps,
            max_width=settings.rulecam_connectors_max_width,
            keep_chunks=settings.rulecam_connectors_keep_chunks,
            clip_roots=clip_roots(settings.rulecam_data_dir, extra_clip_roots),
        ),
        bus=bus,
        clock=clock,
        surveyor=surveyor,
        chunks=chunks,
    )
