"""Chain a `coverage` record whenever a camera goes BLIND or comes back.

The coverage watchdog (`perception.coverage`, P1-08) decides from each chunk's frames whether a
camera is frozen, dark, blocked or moved, with hysteresis. Until now nothing ran it on the live
feed, so evidence packs could only say blind time was "unknown". `CoverageLog` runs it on every
new chunk of every camera (the first chunk's first frame is the camera's reference) and appends
one `coverage` record per confirmed change: camera id, `state` (`blind` / `ok`), `reason`,
`moved` and the change's camera time. It only records: it changes no rule decision.

The API runs `poll` back to back in a worker thread (`AppServices.pollers`).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from rulecam.contracts import Chunk, EventRecord
from rulecam.log import get_logger
from rulecam.perception.coverage import CoverageWatchdog, make_reference, read_frames

_log = get_logger(__name__)

POLL_S = 1.0
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ChunkLister(Protocol):
    """The recorded chunks per camera (`runtime.factory.ChunkIndex`)."""

    def last_seen(self) -> dict[str, datetime]:
        """Each camera's latest chunk start."""
        ...

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        """The camera's chunks overlapping `[t0, t1]`, oldest first."""
        ...


class CoverageChain(Protocol):
    """Appends one record to the evidence chain (`EventChain.append`)."""

    def append(self, kind: Literal["coverage"], payload: Mapping[str, object]) -> EventRecord:
        """Append and return the record."""
        ...


class CoverageLog:
    """Runs the coverage watchdog on new chunks and chains each confirmed change."""

    def __init__(
        self,
        chunks: ChunkLister,
        chain: CoverageChain,
        *,
        watchdog: CoverageWatchdog | None = None,
        sleep: Callable[[float], None] = time.sleep,
        poll_s: float = POLL_S,
    ) -> None:
        """Create the log.

        Args:
            chunks: The recorded chunks.
            chain: The site's evidence chain.
            watchdog: The watchdog (a default one, publishing nothing, when omitted).
            sleep: Waits between polls (injected in tests).
            poll_s: Seconds between polls.
        """
        self._chunks = chunks
        self._chain = chain
        self._watchdog = watchdog or CoverageWatchdog()
        self._sleep = sleep
        self._poll_s = poll_s
        self._done: dict[str, datetime] = {}

    def poll(self) -> None:
        """Observe every camera's new chunks, then wait `poll_s`."""
        for camera_id, last in sorted(self._chunks.last_seen().items()):
            since = self._done.get(camera_id)
            for chunk in self._chunks.chunks(camera_id, since or EPOCH, last):
                if since is not None and chunk.start_ts <= since:
                    continue
                self.observe(chunk)
                self._done[camera_id] = since = chunk.start_ts
        self._sleep(self._poll_s)

    def observe(self, chunk: Chunk) -> EventRecord | None:
        """Observe one chunk; chain and return a `coverage` record if the state changed."""
        try:
            frames = read_frames(Path(chunk.path), chunk.n_frames)
            if chunk.camera_id not in self._done:
                self._watchdog.set_reference(chunk.camera_id, make_reference(frames[0]))
            verdict = self._watchdog.observe(chunk, frames)
        except (ValueError, OSError) as exc:  # unreadable or out-of-order chunk: skip it
            _log.warning("coverage_chunk_skipped", chunk_id=chunk.chunk_id, error=str(exc))
            return None
        if not verdict.changed:
            return None
        record = self._chain.append(
            "coverage",
            {
                "camera_id": chunk.camera_id,
                "state": verdict.state.value,
                "reason": verdict.reason.value if verdict.reason is not None else None,
                "moved": verdict.reason is not None and verdict.reason.value == "moved",
                "chunk_id": chunk.chunk_id,
                "ts": (chunk.start_ts).isoformat(),
            },
        )
        _log.info(
            "coverage_changed",
            camera_id=chunk.camera_id,
            state=verdict.state.value,
            reason=verdict.reason.value if verdict.reason is not None else None,
        )
        return record
