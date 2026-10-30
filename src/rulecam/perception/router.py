"""Segmenter routing: hosted SAM 3.1 to plan, self-hosted SAM 3.1 to run (task P1-03, MP §10).

- Planner probes and surveys, and Rule CI, always use hosted SAM 3.1.
- Continuous runtime uses the site's configured backend, self-hosted by default.
- When self-hosted fails (`SamRuntimeError`: Modal down, app not deployed, bad response), runtime
  calls fall back to hosted. The first failure of an outage logs a warning and calls the alert
  hook once; for `retry_after_s` afterwards every call goes straight to hosted, then self-hosted
  is tried again. Invalid prompts (`ValueError`) are re-raised and never fall back.

Runtime calls served by hosted SAM are recorded in `PerceptionMetrics`; self-hosted calls are
recorded by `ModalSam31` itself, together with their GPU-seconds.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import Field

from rulecam.contracts.core import Chunk, Detection, Frozen
from rulecam.contracts.protocols import Clock, Segmenter
from rulecam.log import get_logger
from rulecam.perception.errors import SamRuntimeError
from rulecam.perception.metrics import Backend, PerceptionMetrics
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
    ) -> None:
        """Create the router.

        Args:
            hosted: Hosted SAM 3.1 (`HostedSam31`, P1-02).
            self_hosted: Self-hosted SAM 3.1 (`ModalSam31`).
            clock: Used for the fallback retry window.
            policy: Per-site settings (defaults to `RoutingPolicy()`).
            metrics: Records runtime calls served by hosted SAM.
            on_fallback: Alert hook for runtime fallbacks.
        """
        self._policy = policy or RoutingPolicy()
        self._hosted = hosted
        metered_hosted = MeteredSegmenter(hosted, metrics)
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
