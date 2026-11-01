"""Model spend in dollars per camera: the hourly budget, its alarm, and cost per camera-hour (WS3).

Two things cost money while the runtime watches cameras:

- **hosted SAM 3.1**, priced with `sam_cost` (video: $0.20 per 1,000 billed frames, at least 50
  per request; images: $2.50 per 1,000). The bill comes from `PerceptionMetrics.hosted_usage`,
  which counts only the calls hosted SAM really served (including self-hosted fallbacks);
- **the GPU pods** (self-hosted SAM, Glimmer), billed by the hour whether busy or not:
  `RULECAM_GPU_USD_PER_HOUR`, shared equally by the cameras that are active (a camera with a rule
  of the active plan that delivered a chunk in the last minute). GPU time while no camera is
  active is reported as `gpu_idle_usd`.

`CostMeter.tick` (once a second, from the API's ticker) samples the bill. A camera's **spend
rate** is its hosted spend over the last `window_s` (60 s), scaled to an hour, plus its GPU
share. At or above `RULECAM_BUDGET_USD_PER_CAMERA_HOUR` the camera is **over budget**: the alarm
is raised (logged `cost_budget_exceeded`, shown in `/runtime/metrics` → `cost.alarm` and as a red
badge in the UI header). If hosted spend is part of the rate, the camera also **degrades**: the
runtime's sampler segments one chunk in `RULECAM_SAM_BUDGET_STRIDE` (never while a HIGH rule's
camera is active) until the rate falls under the budget. GPU time alone never degrades a camera,
because segmenting less would not lower it.

**Cost per camera-hour** is everything spent (hosted + GPU, idle GPU time included) divided by
the camera time covered, overall and per camera.

`CostWatch` is the API's ticker: it finds the active cameras, ticks the meter and the optional
idle GPU stop (`runtime.idle_stop`), and builds the report.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import Field

from rulecam.clients.meta_hosted.sam_cost import USD_PER_IMAGE, USD_PER_VIDEO_FRAME
from rulecam.config import Settings
from rulecam.contracts import Clock, Frozen
from rulecam.log import get_logger
from rulecam.perception.metrics import HostedUsage
from rulecam.runtime.idle_stop import IdleGpuStop, IdleStopStatus
from rulecam.runtime.loop import ActivePlan

_log = get_logger(__name__)

SECONDS_PER_HOUR = 3600.0
DEFAULT_WINDOW_S = 60.0
ACTIVE_CAMERA_S = 60.0
MAX_TICK_GAP_S = 10.0  # a longer gap (the process slept) accrues no GPU time


class CostSettings(Frozen):
    """The dollar budget and prices (from `Settings`).

    Attributes:
        budget_usd_per_camera_hour: Spend rate per camera that raises the alarm; 0 turns it off.
        gpu_usd_per_hour: The GPU pods' combined hourly price, shared by active cameras.
        window_s: The window the spend rate is measured over.
    """

    budget_usd_per_camera_hour: float = Field(default=0.0, ge=0)
    gpu_usd_per_hour: float = Field(default=0.0, ge=0)
    window_s: float = Field(default=DEFAULT_WINDOW_S, gt=0)

    @classmethod
    def from_settings(cls, settings: Settings) -> CostSettings:
        """Read `RULECAM_BUDGET_USD_PER_CAMERA_HOUR` and `RULECAM_GPU_USD_PER_HOUR`."""
        return cls(
            budget_usd_per_camera_hour=settings.rulecam_budget_usd_per_camera_hour,
            gpu_usd_per_hour=settings.rulecam_gpu_usd_per_hour,
        )


def hosted_usd(usage: HostedUsage) -> float:
    """Price a hosted bill with `sam_cost`: billed video frames plus single images."""
    return usage.billed_frames * USD_PER_VIDEO_FRAME + usage.images * USD_PER_IMAGE


def active_cameras(
    plan: ActivePlan | None,
    last_seen: Mapping[str, datetime],
    now: datetime,
    within_s: float = ACTIVE_CAMERA_S,
) -> frozenset[str]:
    """Cameras with a rule of the active plan that delivered a chunk in the last `within_s`."""
    if plan is None:
        return frozenset()
    horizon = now - timedelta(seconds=within_s)
    return frozenset(cam for cam, ts in last_seen.items() if ts >= horizon and plan.rules_on(cam))


class CameraSpend(Frozen):
    """One camera's spend.

    Attributes:
        camera_id: The camera.
        usd_per_hour: Current spend rate (hosted over the window, scaled, plus its GPU share).
        hosted_usd_per_hour: The hosted part of the rate.
        gpu_usd_per_hour: Its share of the GPU pods (0 while inactive).
        usd: Spent so far (hosted plus GPU share).
        camera_hours: Camera time covered.
        usd_per_camera_hour: `usd / camera_hours`, once there is camera time.
        over_budget: The rate is at or above the budget.
        degraded: Over budget with hosted spend: the sampler thins its chunks.
    """

    camera_id: str
    usd_per_hour: float
    hosted_usd_per_hour: float
    gpu_usd_per_hour: float
    usd: float
    camera_hours: float
    usd_per_camera_hour: float | None
    over_budget: bool
    degraded: bool


class CostReport(Frozen):
    """`/runtime/metrics` → `cost`.

    Attributes:
        budget_usd_per_camera_hour: The budget (0: off).
        gpu_usd_per_hour: The GPU pods' price in use (0: not priced).
        alarm: Some camera is over budget.
        over_budget: Those cameras.
        usd: Everything spent so far (hosted, GPU shares and idle GPU time).
        gpu_idle_usd: GPU time while no camera was active.
        camera_hours: Camera time covered, all cameras.
        usd_per_camera_hour: `usd / camera_hours`, once there is camera time.
        cameras: Per camera.
        idle_stop: The idle GPU stop, when the API runs one.
    """

    budget_usd_per_camera_hour: float
    gpu_usd_per_hour: float
    alarm: bool
    over_budget: tuple[str, ...] = ()
    usd: float = 0.0
    gpu_idle_usd: float = 0.0
    camera_hours: float = 0.0
    usd_per_camera_hour: float | None = None
    cameras: tuple[CameraSpend, ...] = ()
    idle_stop: IdleStopStatus | None = None


@dataclass
class _Camera:
    samples: deque[tuple[datetime, float]] = field(default_factory=deque)  # (when, hosted usd)
    gpu_usd: float = 0.0
    seconds: float = 0.0
    hosted_rate: float = 0.0
    gpu_rate: float = 0.0
    over: bool = False
    degraded: bool = False


class CostMeter:
    """Samples the hosted bill, accrues GPU time and decides who is over budget."""

    def __init__(
        self,
        settings: CostSettings,
        *,
        usage: Callable[[], Mapping[str, HostedUsage]],
    ) -> None:
        """Create the meter.

        Args:
            settings: Budget and prices.
            usage: Each camera's cumulative hosted bill (`PerceptionMetrics.hosted_usage`).
        """
        self._settings = settings
        self._usage = usage
        self._cameras: dict[str, _Camera] = {}
        self._gpu_idle_usd = 0.0
        self._last_tick: datetime | None = None
        self._lock = threading.Lock()

    @property
    def settings(self) -> CostSettings:
        """Budget and prices in use."""
        return self._settings

    def over_budget(self, camera_id: str) -> bool:
        """Whether the sampler should degrade `camera_id` (over budget with hosted spend)."""
        with self._lock:
            cam = self._cameras.get(camera_id)
            return cam is not None and cam.degraded

    def tick(self, now: datetime, active: Collection[str]) -> None:
        """Sample the bill at `now` and re-check every camera against the budget.

        Args:
            now: The current time.
            active: Cameras with an active plan that delivered a chunk recently.
        """
        usage = self._usage()
        with self._lock:
            for camera_id in set(usage) | set(active):
                # A camera starts from nothing spent: its first bill counts in its first window.
                self._cameras.setdefault(camera_id, _Camera(samples=deque([(now, 0.0)])))
            self._accrue_gpu(self._elapsed(now), active)
            for camera_id in sorted(set(usage) | set(active)):
                cam = self._cameras[camera_id]
                bill = usage.get(camera_id, HostedUsage())
                cam.seconds = bill.seconds
                self._rate(cam, now, hosted_usd(bill))
                cam.gpu_rate = self._gpu_share(active) if camera_id in active else 0.0
                self._check(camera_id, cam)

    def _elapsed(self, now: datetime) -> float:
        last, self._last_tick = self._last_tick, now
        if last is None:
            return 0.0
        dt = (now - last).total_seconds()
        return dt if 0 < dt <= MAX_TICK_GAP_S else 0.0

    def _gpu_share(self, active: Collection[str]) -> float:
        return self._settings.gpu_usd_per_hour / len(active) if active else 0.0

    def _accrue_gpu(self, dt: float, active: Collection[str]) -> None:
        spent = self._settings.gpu_usd_per_hour * dt / SECONDS_PER_HOUR
        if not active:
            self._gpu_idle_usd += spent
            return
        for camera_id in active:
            self._cameras[camera_id].gpu_usd += spent / len(active)

    def _rate(self, cam: _Camera, now: datetime, usd: float) -> None:
        cam.samples.append((now, usd))
        horizon = now - timedelta(seconds=self._settings.window_s)
        while len(cam.samples) > 1 and cam.samples[1][0] <= horizon:
            cam.samples.popleft()
        spent = usd - cam.samples[0][1]
        cam.hosted_rate = spent * SECONDS_PER_HOUR / self._settings.window_s

    def _check(self, camera_id: str, cam: _Camera) -> None:
        budget = self._settings.budget_usd_per_camera_hour
        rate = cam.hosted_rate + cam.gpu_rate
        over = budget > 0 and rate >= budget
        if over and not cam.over:
            _log.warning(
                "cost_budget_exceeded",
                camera_id=camera_id,
                usd_per_hour=round(rate, 4),
                budget_usd_per_camera_hour=budget,
                degrade=cam.hosted_rate > 0,
            )
        elif cam.over and not over:
            _log.info("cost_budget_recovered", camera_id=camera_id, usd_per_hour=round(rate, 4))
        cam.over = over
        cam.degraded = over and cam.hosted_rate > 0

    def report(self) -> CostReport:
        """The spend so far and who is over budget."""
        with self._lock:
            cameras = tuple(
                _spend(camera_id, cam) for camera_id, cam in sorted(self._cameras.items())
            )
            idle = self._gpu_idle_usd
        total = sum(c.usd for c in cameras) + idle
        hours = sum(c.camera_hours for c in cameras)
        over = tuple(c.camera_id for c in cameras if c.over_budget)
        return CostReport(
            budget_usd_per_camera_hour=self._settings.budget_usd_per_camera_hour,
            gpu_usd_per_hour=self._settings.gpu_usd_per_hour,
            alarm=bool(over),
            over_budget=over,
            usd=round(total, 6),
            gpu_idle_usd=round(idle, 6),
            camera_hours=round(hours, 6),
            usd_per_camera_hour=round(total / hours, 6) if hours > 0 else None,
            cameras=cameras,
        )


def _spend(camera_id: str, cam: _Camera) -> CameraSpend:
    hosted = cam.samples[-1][1] if cam.samples else 0.0
    usd = hosted + cam.gpu_usd
    hours = cam.seconds / SECONDS_PER_HOUR
    return CameraSpend(
        camera_id=camera_id,
        usd_per_hour=round(cam.hosted_rate + cam.gpu_rate, 6),
        hosted_usd_per_hour=round(cam.hosted_rate, 6),
        gpu_usd_per_hour=round(cam.gpu_rate, 6),
        usd=round(usd, 6),
        camera_hours=round(hours, 6),
        usd_per_camera_hour=round(usd / hours, 6) if hours > 0 else None,
        over_budget=cam.over,
        degraded=cam.degraded,
    )


class CostWatch:
    """Ticks the meter and the idle GPU stop with the cameras that are active right now."""

    def __init__(
        self,
        meter: CostMeter,
        *,
        clock: Clock,
        plan: Callable[[], ActivePlan | None],
        last_seen: Callable[[], Mapping[str, datetime]],
        idle_stop: IdleGpuStop | None = None,
    ) -> None:
        """Create the watch.

        Args:
            meter: The cost meter.
            clock: Time source.
            plan: The runtime's active plan (`RuntimeLoop.plan`).
            last_seen: Each camera's latest chunk start (`ChunkIndex.last_seen`).
            idle_stop: Stops the GPU pods after a long idle period, if configured.
        """
        self._meter = meter
        self._clock = clock
        self._plan = plan
        self._last_seen = last_seen
        self._idle_stop = idle_stop

    def tick(self) -> None:
        """Called once a second by the API."""
        now = self._clock.now()
        active = active_cameras(self._plan(), self._last_seen(), now)
        self._meter.tick(now, active)
        if self._idle_stop is not None:
            self._idle_stop.tick(now, active)

    def report(self) -> CostReport:
        """The meter's report with the idle stop's status."""
        status = self._idle_stop.status() if self._idle_stop is not None else None
        return self._meter.report().model_copy(update={"idle_stop": status})
