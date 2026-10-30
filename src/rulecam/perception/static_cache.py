"""Static concept cache: segment sinks, doors and racks once per calibration (task P1-03).

Concepts whose `ConceptSpec.static` is true do not move, so they are segmented once per camera
and calibration and served from memory afterwards. Each cached object keeps its most confident
mask. Every lookup, hit or miss, returns one `Detection` per object re-stamped to the requesting
chunk's first frame (`frame_idx=0`, `ts=chunk.start_ts`): one mask stands for the whole chunk.

The cache is dropped for a camera when the camera moves. It listens on `Topic.COVERAGE_CHANGED`,
published by the coverage watchdog (P1-08), and expects this payload, as a dict or any object
with these attributes:

    {"camera_id": "<camera id>", "state": "moved"}

The coverage watchdog (PR #6) actually publishes `BLIND(reason)` changes:

    {"camera_id": "cam1", "state": "blind", "reason": "moved", "moved": true, "ts": "..."}

A camera's entries are invalidated when `moved` is true, `reason` is `"moved"` or `state` is
`"moved"`. Any other change (OK, dark, frozen, blocked) keeps the cache.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from contextlib import aclosing
from dataclasses import dataclass

from rulecam.bus import Bus, Topic
from rulecam.contracts.core import Chunk, Detection
from rulecam.contracts.protocols import Segmenter
from rulecam.log import get_logger

MOVED = "moved"

_log = get_logger(__name__)


@dataclass(frozen=True)
class _Entry:
    calibration: str | None
    detections: tuple[Detection, ...]


def representatives(detections: list[Detection]) -> tuple[Detection, ...]:
    """Keep the most confident detection of each object (the later frame on a tie).

    Args:
        detections: Detections of one static concept in one chunk.

    Returns:
        One detection per object id, sorted by object id.
    """
    best: dict[int, Detection] = {}
    for detection in sorted(detections, key=lambda d: d.frame_idx):
        current = best.get(detection.object_id)
        if current is None or _confidence(detection) >= _confidence(current):
            best[detection.object_id] = detection
    return tuple(best[object_id] for object_id in sorted(best))


def restamp(detections: tuple[Detection, ...], chunk: Chunk) -> list[Detection]:
    """Place cached detections on the first frame of `chunk`.

    Args:
        detections: Cached representatives.
        chunk: The chunk being served.

    Returns:
        Copies with `frame_idx=0` and `ts=chunk.start_ts`.
    """
    return [d.model_copy(update={"frame_idx": 0, "ts": chunk.start_ts}) for d in detections]


def moved_camera(payload: object) -> str | None:
    """Return the camera id if a `coverage.changed` payload says the camera moved.

    Args:
        payload: A dict or an object with `camera_id`, `state`, and optionally `reason`, `moved`.

    Returns:
        The camera id, or `None` if the payload is not a "moved" change.
    """
    camera_id = _field(payload, "camera_id")
    if not isinstance(camera_id, str):
        return None
    if _field(payload, "moved") is True:
        return camera_id
    for name in ("state", "reason"):
        value = _field(payload, name)
        if isinstance(value, str) and value.lower() == MOVED:
            return camera_id
    return None


class StaticCache:
    """Caches static-concept detections per camera, concept and calibration."""

    def __init__(self, segmenter: Segmenter) -> None:
        """Create an empty cache.

        Args:
            segmenter: Used on a miss (usually the runtime segmenter from the router).
        """
        self._segmenter = segmenter
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._generation: dict[str, int] = {}  # bumped on invalidation, guards in-flight misses

    def __len__(self) -> int:
        """Return the number of cached (camera, concept) entries."""
        with self._lock:
            return len(self._entries)

    def get(self, chunk: Chunk, concept: str, *, calibration: str | None = None) -> list[Detection]:
        """Return the static concept's detections for `chunk`, segmenting only on a miss.

        Args:
            chunk: The chunk being processed.
            concept: A static concept.
            calibration: The camera's calibration id; a different id is a miss.

        Returns:
            One detection per cached object, on the chunk's first frame.
        """
        key = (chunk.camera_id, concept)
        with self._lock:
            entry = self._entries.get(key)
            generation = self._generation.get(chunk.camera_id, 0)
        if entry is not None and entry.calibration == calibration:
            return restamp(entry.detections, chunk)
        detections = representatives(self._segmenter.segment(chunk, concept))
        with self._lock:
            if self._generation.get(chunk.camera_id, 0) == generation:
                self._entries[key] = _Entry(calibration, detections)
        _log.info(
            "static_cache_filled",
            camera_id=chunk.camera_id,
            concept=concept,
            calibration=calibration,
            objects=len(detections),
        )
        return restamp(detections, chunk)

    def invalidate(self, camera_id: str) -> int:
        """Drop every entry of one camera.

        Args:
            camera_id: The camera.

        Returns:
            How many entries were dropped.
        """
        with self._lock:
            self._generation[camera_id] = self._generation.get(camera_id, 0) + 1
            keys = [key for key in self._entries if key[0] == camera_id]
            for key in keys:
                del self._entries[key]
        _log.info("static_cache_invalidated", camera_id=camera_id, entries=len(keys))
        return len(keys)

    def handle_coverage_changed(self, payload: object) -> bool:
        """Invalidate a camera if the payload says it moved.

        Args:
            payload: A `coverage.changed` payload (see the module docstring).

        Returns:
            True if the payload was a "moved" change.
        """
        camera_id = moved_camera(payload)
        if camera_id is None:
            return False
        self.invalidate(camera_id)
        return True

    async def run(self, bus: Bus) -> None:
        """Listen on `coverage.changed` and invalidate moved cameras until cancelled.

        Args:
            bus: The event bus.
        """
        async with aclosing(bus.subscribe(Topic.COVERAGE_CHANGED)) as messages:
            async for message in messages:
                self.handle_coverage_changed(message.payload)


def _confidence(detection: Detection) -> float:
    confidence = detection.mask.confidence
    return -1.0 if confidence is None else confidence


def _field(payload: object, name: str) -> object:
    if isinstance(payload, Mapping):
        return payload.get(name)
    return getattr(payload, name, None)
