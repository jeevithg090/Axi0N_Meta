"""Hedged SAM calls: a call that hangs gets a twin, and the first answer wins.

Live on 4 Nov the planner's SAM calls answered in about 2 s, but now and then one request hung
until the transport gave up 40 s later, and a probe of 24 frames waited for its slowest frame.
`HedgedSegmenter` sends the same request again when the first has not answered after
`after_s` (15 s: at 8 s a busy API got 198 twins in three plans, which only added load), and
returns whichever answers first; a call that fails while its twin is still running waits for
the twin. The answer is the same SAM answer either way (same frame, same
concept), so no check changes; a hedge costs one extra image call.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Final

from rulecam.contracts.core import Detection
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter

_log = get_logger(__name__)

HEDGE_AFTER_S: Final = 15.0  # far past a normal call (about 2 s): only hung calls get a twin
MAX_IN_FLIGHT: Final = 96


class HedgedSegmenter:
    """An `ImageSegmenter` that re-sends a call that has not answered after `after_s`."""

    def __init__(
        self,
        inner: ImageSegmenter,
        *,
        after_s: float = HEDGE_AFTER_S,
        pool: ThreadPoolExecutor | None = None,
    ) -> None:
        """Wrap `inner`.

        Args:
            inner: SAM 3.1 on single images.
            after_s: How long to wait before sending the twin.
            pool: Runs the calls (a shared one by default, sized for the planner's fan-out).
        """
        self._inner = inner
        self._after_s = after_s
        self._pool = pool or ThreadPoolExecutor(
            max_workers=MAX_IN_FLIGHT, thread_name_prefix="sam-hedge"
        )

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Segment through `inner`, hedging a slow call."""

        def call() -> list[Detection]:
            return self._inner.segment_image(
                frame, concept, camera_id=camera_id, include_confidence=include_confidence
            )

        first = self._pool.submit(call)
        try:
            return first.result(timeout=self._after_s)
        except FutureTimeout:
            _log.info("sam_hedged", concept=concept, camera_id=camera_id, after_s=self._after_s)
        return _first_answer([first, self._pool.submit(call)])


def _first_answer(futures: list[Future[list[Detection]]]) -> list[Detection]:
    """The first future to succeed; the last error if all fail."""
    pending = set(futures)
    error: BaseException | None = None
    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            exc = future.exception()
            if exc is None:
                return future.result()
            error = exc
    assert error is not None  # noqa: S101 - every future finished and none succeeded
    raise error
