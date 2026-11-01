"""Per-stage timings of the runtime loop (task P3-08): where a chunk's seconds go.

Every chunk the loop segments is timed stage by stage, on a monotonic clock:

| stage | from | to |
|---|---|---|
| `queued` | `chunk.ready` received | submission starts (a full pipeline waits here) |
| `admit` | submission starts | lag, sampling, motion gate and budget decided |
| `sam_queue` | a concept asks for a hosted SAM slot | it gets one (`RULECAM_SAM_MAX_CONCURRENCY`) |
| `upload` | the chunk's file upload starts | it ends (once per chunk, shared by its concepts) |
| `sam_request` | one SAM request is sent | its reply is parsed |
| `sam` | a concept's segmentation starts (after its slot) | it ends, upload included |
| `segment` | the concept pass starts | every concept is back |
| `reorder_wait` | the concept pass ends | the in-order stage starts (earlier chunks first) |
| `stitch` | identity hand-off starts | tracks stored |
| `rules` | rule evaluation starts | candidates found |
| `candidates` | new candidates recorded, strips rendered, act-first actions run | done |
| `chain` | evidence-chain appends of the chunk (summed) | |
| `verify_queue` | a candidate is recorded | its Tier 1 verification starts |
| `verify` | Tier 1 verification starts | its verdict is back |
| `decide` | the verdict is back | verdict, decision and actions chained |
| `end_to_end` | `chunk.ready` received | the in-order stage is done |

`queued`, `admit`, `reorder_wait`, `stitch`, `rules`, `candidates` and `end_to_end` are
recorded once per segmented chunk; the SAM stages once per concept or request; the verification
stages once per candidate. A chunk's whole timeline is logged when its in-order stage ends
(`runtime_chunk_timing`), and `/runtime/metrics` serves each stage's p50/p95 over its last
`samples` values.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from rulecam.contracts import Frozen
from rulecam.log import get_logger

_log = get_logger(__name__)

STAGES: tuple[str, ...] = (
    "queued",
    "admit",
    "sam_queue",
    "upload",
    "sam_request",
    "sam",
    "segment",
    "reorder_wait",
    "stitch",
    "rules",
    "candidates",
    "chain",
    "verify_queue",
    "verify",
    "decide",
    "end_to_end",
)
DEFAULT_SAMPLES = 200
MAX_OPEN_CHUNKS = 256

StageSink = Callable[[str, str, str, float], None]
"""`(stage, camera_id, chunk_id, seconds)`: told one measured stage of one chunk."""


class StageStats(Frozen):
    """One stage's recent durations: how many, and their p50/p95 in seconds."""

    n: int = 0
    p50_s: float = 0.0
    p95_s: float = 0.0


def percentile(samples: Sequence[float], q: float) -> float:
    """Nearest-rank percentile of `samples` (0 when there are none).

    Args:
        samples: The values.
        q: The percentile, in (0, 100].

    Returns:
        The smallest sample with at least `q` percent of the samples at or below it.
    """
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


@dataclass
class _Timeline:
    stages: dict[str, float] = field(default_factory=dict)
    sam: dict[str, float] = field(default_factory=dict)  # concept -> seconds


class StageTimings:
    """Thread-safe per-stage duration samples, and each open chunk's timeline for the log."""

    def __init__(
        self,
        samples: int = DEFAULT_SAMPLES,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        max_open: int = MAX_OPEN_CHUNKS,
    ) -> None:
        """Create empty timings.

        Args:
            samples: Durations kept per stage for the percentiles.
            monotonic: Seconds on a monotonic clock (injected in tests).
            max_open: Chunk timelines kept until their chunk finishes; the oldest are dropped.
        """
        self._samples = max(1, samples)
        self._monotonic = monotonic
        self._max_open = max(1, max_open)
        self._lock = threading.Lock()
        self._durations: dict[str, deque[float]] = {}
        self._open: OrderedDict[tuple[str, str], _Timeline] = OrderedDict()

    def now(self) -> float:
        """The current monotonic time in seconds."""
        return self._monotonic()

    def record(
        self, stage: str, camera_id: str, chunk_id: str, seconds: float, *, key: str | None = None
    ) -> None:
        """Add one measured duration of `stage` for a chunk (a `StageSink`).

        Durations of the same stage on the same timeline (e.g. several chain appends) are summed
        there; each one is a separate percentile sample.

        Args:
            stage: The stage name (see the module docstring).
            camera_id: The chunk's camera.
            chunk_id: The chunk.
            seconds: How long it took (negative values count as 0).
            key: The timeline to add it to (default: the chunk's), e.g. a candidate's event id.
        """
        seconds = max(0.0, seconds)
        with self._lock:
            self._durations.setdefault(stage, deque(maxlen=self._samples)).append(seconds)
            timeline = self._timeline(camera_id, key or chunk_id)
            timeline.stages[stage] = timeline.stages.get(stage, 0.0) + seconds

    def record_sam(self, camera_id: str, chunk_id: str, concept: str, seconds: float) -> None:
        """Record one concept's segmentation (`sam`), kept per concept on the chunk's timeline."""
        self.record("sam", camera_id, chunk_id, seconds)
        with self._lock:
            timeline = self._timeline(camera_id, chunk_id)
            timeline.sam[concept] = round(max(0.0, seconds), 3)

    def finish(
        self,
        camera_id: str,
        chunk_id: str,
        *,
        key: str | None = None,
        event: str = "runtime_chunk_timing",
    ) -> dict[str, object]:
        """Close a timeline, log it as `event` and return it.

        Args:
            camera_id: The chunk's camera.
            chunk_id: The chunk.
            key: The timeline (default: the chunk's); logged as `key` when given.
            event: The log event name.

        Returns:
            Stage -> seconds (rounded to ms), plus `sam_by_concept` when concepts were timed.
        """
        with self._lock:
            timeline = self._open.pop((camera_id, key or chunk_id), _Timeline())
        fields: dict[str, object] = {k: round(v, 3) for k, v in timeline.stages.items()}
        if timeline.sam:
            fields["sam_by_concept"] = dict(timeline.sam)
        if key is not None:
            fields["key"] = key
        _log.info(event, camera_id=camera_id, chunk_id=chunk_id, **fields)
        return fields

    def report(self) -> dict[str, StageStats]:
        """Each stage's p50/p95 over its recent samples, in pipeline order (unseen stages: 0)."""
        with self._lock:
            snapshot = {stage: list(values) for stage, values in self._durations.items()}
        names = list(STAGES) + sorted(set(snapshot) - set(STAGES))
        return {name: _stats(snapshot.get(name, [])) for name in names}

    def _timeline(self, camera_id: str, key: str) -> _Timeline:
        """The open timeline `(camera_id, key)`; call with the lock held."""
        slot = (camera_id, key)
        timeline = self._open.get(slot)
        if timeline is None:
            timeline = self._open[slot] = _Timeline()
            while len(self._open) > self._max_open:
                self._open.popitem(last=False)
        return timeline


def _stats(values: Sequence[float]) -> StageStats:
    return StageStats(
        n=len(values),
        p50_s=round(percentile(values, 50), 3),
        p95_s=round(percentile(values, 95), 3),
    )
