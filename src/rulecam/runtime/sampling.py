"""Which chunks of a camera get segmented: idle sampling and the SAM budget (task P3-08).

Hosted SAM 3.1 bills at least 50 frames per request and takes one concept per request, so a
camera running two concepts on every 3 s chunk bills about 2,000 frames a minute. This module
thins that out without letting a real event through:

- **Idle sampling.** While a camera is *idle* (no candidate, no rule-relevant moving object in
  view and, for HIGH rules, no motion), one chunk in `idle_stride` is segmented. The stride is the
  strictest one of the severities on that camera (`low`, `medium`: 3 by default; `high`: 2 at
  most). A camera becomes *hot* when a candidate opens, when a dynamic concept is tracked, or, if
  a HIGH rule runs on it, when the motion gate sees motion. A hot camera is segmented on every
  chunk until `hot_holdoff_s` after the last activity.
- **HIGH rules are never thinned while something happens.** On a camera with a HIGH rule, a chunk
  with motion (or unknown motion) or a hot camera is always segmented, whatever the budget.
- **Budget.** When a camera's billed frames over the last minute reach `budget_frames_per_min`,
  it degrades to one chunk in `budget_stride` (logged `budget_exceeded`) instead of falling behind,
  until the minute's spend drops under the budget again. Chunks still being segmented count
  with their `reserve`d estimate until `record` replaces it with what they billed, so a loop
  with several chunks in flight cannot overspend the budget before the bills come in.
  The main budget is in **dollars** (`runtime.cost.CostMeter`, passed as `over_budget`): a camera
  it marks over budget degrades the same way. The frame budget is an optional extra cap.

Every chunk that is not segmented is a coverage gap for the loop, never an observed absence.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field

from rulecam.config import Settings
from rulecam.contracts import Chunk, Frozen, RuleIR, Severity
from rulecam.log import get_logger

_log = get_logger(__name__)

BUDGET_WINDOW = timedelta(minutes=1)

Admission = Literal["run", "idle", "budget"]


def chunk_end(chunk: Chunk) -> datetime:
    """Return when a chunk's last frame ends."""
    return chunk.start_ts + timedelta(seconds=chunk.n_frames / chunk.fps)


class SamplingPolicy(Frozen):
    """Idle sampling and budget settings (from `Settings`; see the module docstring).

    The defaults sample nothing out: every chunk runs, as before this policy existed. The
    application's defaults live in `Settings` and are applied by `from_settings`.
    """

    idle_stride_low: int = Field(default=1, ge=1)
    idle_stride_medium: int = Field(default=1, ge=1)
    idle_stride_high: int = Field(default=1, ge=1, le=2)
    hot_holdoff_s: float = Field(default=30.0, ge=0)
    budget_frames_per_min: int = Field(default=0, ge=0)
    budget_stride: int = Field(default=3, ge=1)

    @classmethod
    def from_settings(cls, settings: Settings) -> SamplingPolicy:
        """Build the policy from the `RULECAM_IDLE_*` and `RULECAM_SAM_BUDGET_*` settings."""
        return cls(
            idle_stride_low=settings.rulecam_idle_stride_low,
            idle_stride_medium=settings.rulecam_idle_stride_medium,
            idle_stride_high=settings.rulecam_idle_stride_high,
            hot_holdoff_s=settings.rulecam_idle_holdoff_s,
            budget_frames_per_min=settings.rulecam_sam_budget_frames_per_min,
            budget_stride=settings.rulecam_sam_budget_stride,
        )

    def idle_stride(self, rules: Sequence[RuleIR]) -> int:
        """The strictest idle stride among the severities of `rules` (1 when there are none)."""
        strides = {
            Severity.LOW: self.idle_stride_low,
            Severity.MEDIUM: self.idle_stride_medium,
            Severity.HIGH: self.idle_stride_high,
        }
        return min((strides[rule.severity] for rule in rules), default=1)


def has_high(rules: Sequence[RuleIR]) -> bool:
    """Whether any of `rules` is HIGH severity."""
    return any(rule.severity is Severity.HIGH for rule in rules)


@dataclass
class _Camera:
    since_run: int | None = None  # chunks skipped since the last segmented one; None: never run
    hot_until: datetime | None = None
    degraded: bool = False
    billed: deque[tuple[datetime, int]] = field(default_factory=deque)
    reserved: dict[str, int] = field(default_factory=dict)  # chunk id -> frames, in flight


class CameraSampler:
    """Per-camera sampling state: idle/hot, the last minute's billed frames, budget degrade."""

    def __init__(
        self,
        policy: SamplingPolicy | None = None,
        *,
        over_budget: Callable[[str], bool] | None = None,
    ) -> None:
        """Create the sampler.

        Args:
            policy: The settings (defaults to `SamplingPolicy()`, which samples nothing out).
            over_budget: Whether a camera is over its dollar budget (`CostMeter.over_budget`).
        """
        self._policy = policy or SamplingPolicy()
        self._over_budget = over_budget
        self._cameras: dict[str, _Camera] = {}
        self.budget_exceeded = 0
        """How many times a camera went over its budget (transitions, not chunks)."""

    @property
    def policy(self) -> SamplingPolicy:
        """The settings in force."""
        return self._policy

    def admit(self, chunk: Chunk, rules: Sequence[RuleIR], *, moving: bool) -> Admission:
        """Decide whether to segment `chunk`.

        Args:
            chunk: The next chunk of its camera (chunks of one camera arrive in order).
            rules: The active rules on the chunk's camera (not empty).
            moving: Whether the chunk has motion (`True` when unknown).

        Returns:
            `run`, or why it is skipped: `idle` (idle sampling) or `budget` (over budget).
        """
        cam = self._cameras.setdefault(chunk.camera_id, _Camera())
        self._check_budget(chunk.camera_id, cam, chunk_end(chunk))
        hot = cam.hot_until is not None and chunk.start_ts <= cam.hot_until
        urgent = has_high(rules) and (moving or hot)
        base = 1 if hot else self._policy.idle_stride(rules)
        stride = max(base, self._policy.budget_stride) if cam.degraded else base
        due = cam.since_run is None or cam.since_run + 1 >= stride
        if urgent or due:
            cam.since_run = 0
            return "run"
        cam.since_run = (cam.since_run or 0) + 1
        return "budget" if cam.degraded and cam.since_run >= base else "idle"

    def reserve(self, chunk: Chunk, frames: int) -> None:
        """Count an admitted chunk's expected bill against the budget until it is recorded.

        Args:
            chunk: A chunk `admit` let run, whose segmentation has not finished.
            frames: The frames it is expected to bill.
        """
        if frames > 0:
            self._cameras.setdefault(chunk.camera_id, _Camera()).reserved[chunk.chunk_id] = frames

    def in_flight_frames(self, camera_id: str) -> int:
        """Frames reserved by `camera_id`'s chunks still being segmented."""
        cam = self._cameras.get(camera_id)
        return sum(cam.reserved.values()) if cam is not None else 0

    def record(self, chunk: Chunk, *, billed_frames: int, active: bool) -> None:
        """Record a segmented (or failed) chunk: what it billed, whether something happened.

        Releases the chunk's reservation, if any.

        Args:
            chunk: The chunk just segmented.
            billed_frames: SAM frames billed for it.
            active: A candidate opened, a dynamic concept was tracked, or (HIGH) motion was seen.
        """
        cam = self._cameras.setdefault(chunk.camera_id, _Camera())
        cam.reserved.pop(chunk.chunk_id, None)
        end = chunk_end(chunk)
        if billed_frames:
            cam.billed.append((end, billed_frames))
        if active:
            cam.hot_until = end + timedelta(seconds=self._policy.hot_holdoff_s)

    def billed_last_minute(self, camera_id: str, now: datetime) -> int:
        """Billed frames of `camera_id` in the minute before `now` (stream time)."""
        cam = self._cameras.get(camera_id)
        if cam is None:
            return 0
        _prune(cam, now)
        return sum(frames for _, frames in cam.billed)

    def billed_per_minute(self) -> dict[str, int]:
        """Each camera's billed frames in the last minute of its own stream."""
        return {
            camera_id: self.billed_last_minute(camera_id, cam.billed[-1][0])
            for camera_id, cam in sorted(self._cameras.items())
            if cam.billed
        }

    def degraded(self) -> list[str]:
        """Cameras currently degraded by the budget."""
        return sorted(cam_id for cam_id, cam in self._cameras.items() if cam.degraded)

    def _check_budget(self, camera_id: str, cam: _Camera, now: datetime) -> None:
        dollars = self._over_budget is not None and self._over_budget(camera_id)
        budget = self._policy.budget_frames_per_min
        if budget <= 0 and self._over_budget is None:
            return
        spent = self.billed_last_minute(camera_id, now) + sum(cam.reserved.values())
        over = dollars or (budget > 0 and spent >= budget)
        if over and not cam.degraded:
            self.budget_exceeded += 1
            _log.warning(
                "budget_exceeded",
                camera_id=camera_id,
                billed_frames_last_min=spent,
                budget_frames_per_min=budget,
                dollar_budget=dollars,
                stride=self._policy.budget_stride,
            )
        elif cam.degraded and not over:
            _log.info("budget_recovered", camera_id=camera_id, billed_frames_last_min=spent)
        cam.degraded = over


def _prune(cam: _Camera, now: datetime) -> None:
    while cam.billed and cam.billed[0][0] <= now - BUDGET_WINDOW:
        cam.billed.popleft()
