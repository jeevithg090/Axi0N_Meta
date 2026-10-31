"""Segmenter routing: hosted SAM 3.1 to plan, self-hosted SAM 3.1 to run (task P1-03, MP §10).

- Planner probes and surveys, and Rule CI, always use hosted SAM 3.1.
- Continuous runtime uses the site's configured backend, self-hosted by default.
- When self-hosted fails (`SamRuntimeError`: Modal down, app not deployed, bad response), runtime
  calls fall back to hosted. The first failure of an outage logs a warning and calls the alert
  hook once; for `retry_after_s` afterwards every call goes straight to hosted, then self-hosted
  is tried again. Invalid prompts (`ValueError`) are re-raised and never fall back.

Runtime calls served by hosted SAM are recorded in `PerceptionMetrics`; self-hosted calls are
recorded by `ModalSam31` itself, together with their GPU-seconds. `CallMeter` counts the runtime
loop's own calls and billed frames per camera and per chunk (hosted pricing: `max(50, frames)`
per request), which the loop's SAM budget and `/runtime/metrics` read. `LimitedSegmenter` caps
how many runtime SAM calls are open at once, across cameras and chunks, and reports how long a
call queued for a slot (`sam_queue`) and how long it then took (`sam`).

The runtime path may use its own hosted segmenter (`runtime_hosted`, e.g. the box-only
`HostedSam31.runtime_view()`), while probes and Rule CI keep the full one.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field

from rulecam.contracts.core import Chunk, Detection, Frozen
from rulecam.contracts.protocols import Clock, Segmenter
from rulecam.log import get_logger
from rulecam.perception.errors import SamRuntimeError
from rulecam.perception.metrics import Backend, PerceptionMetrics, hosted_billed_frames
from rulecam.perception.sam_runtime import MAX_PROMPTED_OBJECTS

FallbackAlert = Callable[[str, SamRuntimeError], None]
"""Called as `alert(camera_id, error)` when runtime perception falls back to hosted."""

_log = get_logger(__name__)


class Purpose(StrEnum):
    """Why a segmentation is requested."""

    PROBE = "probe"
    RULE_CI = "rule_ci"
    RUNTIME = "runtime"


class RoutingPolicy(Frozen):
    """Per-site routing settings."""

    runtime_backend: Backend = Backend.SELF_HOSTED
    retry_after_s: float = Field(default=60.0, ge=0)


def hosted_requests(box_prompts: dict[int, tuple[float, float, float, float]] | None) -> int:
    """Return how many hosted requests one `segment` call makes (16 prompted objects each).

    Args:
        box_prompts: The call's box prompts, if any.

    Returns:
        At least 1.
    """
    return max(1, math.ceil(len(box_prompts or {}) / MAX_PROMPTED_OBJECTS))


class MeteredSegmenter:
    """Wraps hosted SAM 3.1 on the runtime path and records each call in `PerceptionMetrics`."""

    def __init__(self, inner: Segmenter, metrics: PerceptionMetrics | None) -> None:
        """Wrap a segmenter.

        Args:
            inner: The hosted segmenter.
            metrics: Where to record calls (nothing is recorded when `None`).
        """
        self._inner = inner
        self._metrics = metrics

    def segment(
        self,
        chunk: Chunk,
        concept: str,
        *,
        box_prompts: dict[int, tuple[float, float, float, float]] | None = None,
        negative_points: list[tuple[int, float, float]] | None = None,
        direction: Literal["forward", "backward"] = "forward",
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Delegate to the wrapped segmenter, then record the hosted requests it made."""
        detections = self._inner.segment(
            chunk,
            concept,
            box_prompts=box_prompts,
            negative_points=negative_points,
            direction=direction,
            include_confidence=include_confidence,
        )
        if self._metrics is not None:
            for _ in range(hosted_requests(box_prompts)):
                self._metrics.record_sam_call(
                    chunk.camera_id, backend=Backend.HOSTED, frames=chunk.n_frames
                )
        return detections


MAX_OPEN_CHUNKS = 1024


def _add(counts: tuple[int, int] | None, requests: int, billed: int) -> tuple[int, int]:
    calls, frames = counts or (0, 0)
    return calls + requests, frames + billed


class CallMeter:
    """Counts the SAM requests a segmenter makes and the frames they bill, per camera.

    Billed frames are priced as hosted SAM 3.1 video (`max(50, frames)` per request), which is
    the real bill when runtime perception is hosted and the hosted-equivalent otherwise. Calls
    are also counted per chunk until `take_chunk` collects them, so a loop with several chunks
    of a camera in flight can still tell what each one cost.
    """

    def __init__(self, inner: Segmenter, *, max_open_chunks: int = MAX_OPEN_CHUNKS) -> None:
        """Wrap a segmenter.

        Args:
            inner: The segmenter whose calls are counted.
            max_open_chunks: Uncollected per-chunk counts kept; the oldest are dropped beyond it.
        """
        self._inner = inner
        self._lock = threading.Lock()
        self._totals: dict[str, tuple[int, int]] = {}
        self._chunks: dict[tuple[str, str], tuple[int, int]] = {}
        self._max_open = max(1, max_open_chunks)

    def totals(self, camera_id: str) -> tuple[int, int]:
        """Return `(requests, billed_frames)` made so far for `camera_id`."""
        with self._lock:
            return self._totals.get(camera_id, (0, 0))

    def take_chunk(self, camera_id: str, chunk_id: str) -> tuple[int, int]:
        """Return `(requests, billed_frames)` made for one chunk and forget them."""
        with self._lock:
            return self._chunks.pop((camera_id, chunk_id), (0, 0))

    def segment(
        self,
        chunk: Chunk,
        concept: str,
        *,
        box_prompts: dict[int, tuple[float, float, float, float]] | None = None,
        negative_points: list[tuple[int, float, float]] | None = None,
        direction: Literal["forward", "backward"] = "forward",
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Delegate, then count the requests the call made."""
        detections = self._inner.segment(
            chunk,
            concept,
            box_prompts=box_prompts,
            negative_points=negative_points,
            direction=direction,
            include_confidence=include_confidence,
        )
        requests = hosted_requests(box_prompts)
        billed = requests * hosted_billed_frames(chunk.n_frames)
        camera, key = chunk.camera_id, (chunk.camera_id, chunk.chunk_id)
        with self._lock:
            self._totals[camera] = _add(self._totals.get(camera), requests, billed)
            self._chunks[key] = _add(self._chunks.pop(key, None), requests, billed)
            while len(self._chunks) > self._max_open:
                del self._chunks[next(iter(self._chunks))]
        return detections


class StageRecorder(Protocol):
    """Receives SAM stage durations (`runtime.timings.StageTimings`)."""

    def record(self, stage: str, camera_id: str, chunk_id: str, seconds: float) -> None:
        """Record one duration of `stage` for a chunk."""
        ...

    def record_sam(self, camera_id: str, chunk_id: str, concept: str, seconds: float) -> None:
        """Record one concept's segmentation of a chunk."""
        ...


class LimitedSegmenter:
    """Caps the SAM calls open at once and times each call's queueing and its work.

    A hosted request's latency grows with the number of requests the team has open, so beyond
    some concurrency more requests only make each one slower. The cap holds the extra calls in
    a queue, measured as the `sam_queue` stage, instead.
    """

    def __init__(
        self,
        inner: Segmenter,
        max_concurrent: int,
        *,
        timings: StageRecorder | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wrap a segmenter.

        Args:
            inner: The segmenter to limit.
            max_concurrent: Calls allowed at once (at least 1).
            timings: Receives `sam_queue` and `sam` (per concept) durations.
            monotonic: Seconds on a monotonic clock (injected in tests).
        """
        self._inner = inner
        self._slots = threading.BoundedSemaphore(max(1, max_concurrent))
        self._timings = timings
        self._monotonic = monotonic
        self.max_concurrent = max(1, max_concurrent)

    def segment(
        self,
        chunk: Chunk,
        concept: str,
        *,
        box_prompts: dict[int, tuple[float, float, float, float]] | None = None,
        negative_points: list[tuple[int, float, float]] | None = None,
        direction: Literal["forward", "backward"] = "forward",
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Wait for a slot, then delegate."""
        asked = self._monotonic()
        with self._slots:
            started = self._monotonic()
            try:
                return self._inner.segment(
                    chunk,
                    concept,
                    box_prompts=box_prompts,
                    negative_points=negative_points,
                    direction=direction,
                    include_confidence=include_confidence,
                )
            finally:
                if self._timings is not None:
                    camera, chunk_id = chunk.camera_id, chunk.chunk_id
                    self._timings.record("sam_queue", camera, chunk_id, started - asked)
                    self._timings.record_sam(camera, chunk_id, concept, self._monotonic() - started)


class FallbackSegmenter:
    """Runtime segmenter: self-hosted first, hosted while self-hosted is failing."""

    def __init__(
        self,
        primary: Segmenter,
        fallback: Segmenter,
        *,
        clock: Clock,
        retry_after_s: float = 60.0,
        on_fallback: FallbackAlert | None = None,
    ) -> None:
        """Create the fallback chain.

        Args:
            primary: Self-hosted SAM 3.1.
            fallback: Hosted SAM 3.1 (usually a `MeteredSegmenter`).
            clock: Decides when to retry the primary.
            retry_after_s: How long to stay on the fallback after a failure.
            on_fallback: Alert hook, called once per outage.
        """
        self._primary = primary
        self._fallback = fallback
        self._clock = clock
        self._retry_after = timedelta(seconds=retry_after_s)
        self._on_fallback = on_fallback
        self._down_until: datetime | None = None

    @property
    def degraded(self) -> bool:
        """True while runtime calls are being served by the fallback."""
        return self._down_until is not None

    def segment(
        self,
        chunk: Chunk,
        concept: str,
        *,
        box_prompts: dict[int, tuple[float, float, float, float]] | None = None,
        negative_points: list[tuple[int, float, float]] | None = None,
        direction: Literal["forward", "backward"] = "forward",
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Segment with the primary, or with the fallback while the primary is down.

        Raises:
            ValueError: If the prompts are invalid (never falls back).
        """
        if self._down_until is None or self._clock.now() >= self._down_until:
            try:
                detections = self._primary.segment(
                    chunk,
                    concept,
                    box_prompts=box_prompts,
                    negative_points=negative_points,
                    direction=direction,
                    include_confidence=include_confidence,
                )
            except SamRuntimeError as exc:
                self._mark_down(chunk.camera_id, exc)
            else:
                self._mark_up(chunk.camera_id)
                return detections
        return self._fallback.segment(
            chunk,
            concept,
            box_prompts=box_prompts,
            negative_points=negative_points,
            direction=direction,
            include_confidence=include_confidence,
        )

    def _mark_down(self, camera_id: str, error: SamRuntimeError) -> None:
        first_failure = self._down_until is None
        self._down_until = self._clock.now() + self._retry_after
        if not first_failure:
            return
        _log.warning("sam_fallback_to_hosted", camera_id=camera_id, error=str(error))
        if self._on_fallback is not None:
            self._on_fallback(camera_id, error)

    def _mark_up(self, camera_id: str) -> None:
        if self._down_until is not None:
            _log.info("sam_self_hosted_recovered", camera_id=camera_id)
        self._down_until = None


class SegmenterRouter:
    """Hands out the right segmenter for each purpose."""

    def __init__(
        self,
        hosted: Segmenter,
        self_hosted: Segmenter,
        *,
        clock: Clock,
        policy: RoutingPolicy | None = None,
        metrics: PerceptionMetrics | None = None,
        on_fallback: FallbackAlert | None = None,
        runtime_hosted: Segmenter | None = None,
    ) -> None:
        """Create the router.

        Args:
            hosted: Hosted SAM 3.1 (`HostedSam31`, P1-02).
            self_hosted: Self-hosted SAM 3.1 (`ModalSam31`).
            clock: Used for the fallback retry window.
            policy: Per-site settings (defaults to `RoutingPolicy()`).
            metrics: Records runtime calls served by hosted SAM.
            on_fallback: Alert hook for runtime fallbacks.
            runtime_hosted: Hosted SAM for the runtime path (and its fallback); `hosted` when
                `None`. Probes and Rule CI always get `hosted`.
        """
        self._policy = policy or RoutingPolicy()
        self._hosted = hosted
        metered_hosted = MeteredSegmenter(runtime_hosted or hosted, metrics)
        self._runtime: Segmenter
        if self._policy.runtime_backend is Backend.SELF_HOSTED:
            self._runtime = FallbackSegmenter(
                self_hosted,
                metered_hosted,
                clock=clock,
                retry_after_s=self._policy.retry_after_s,
                on_fallback=on_fallback,
            )
        else:
            self._runtime = metered_hosted

    @property
    def policy(self) -> RoutingPolicy:
        """The routing policy in force."""
        return self._policy

    def for_purpose(self, purpose: Purpose) -> Segmenter:
        """Return the segmenter to use for `purpose`.

        Args:
            purpose: Probe, Rule CI or runtime.

        Returns:
            Hosted SAM for probes and Rule CI; the runtime chain for continuous perception.
        """
        return self._runtime if purpose is Purpose.RUNTIME else self._hosted
