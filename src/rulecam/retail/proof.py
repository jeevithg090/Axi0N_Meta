"""Closing a restock task with a photo: the models check the gap is really filled.

Staff reply to a restock task with a photo of the bay (`POST /retail/tasks/{id}/proof`). Muse
Spark 1.3 compares the marked "before" image (numbered gaps) with the photo and says, gap by
gap, whether it is filled; Muse Glimmer gives an independent look at the same pair. The task is
closed only when Spark says every gap is filled and Glimmer does not disagree (Glimmer that does
not answer in time, e.g. a cold serverless worker, is recorded and does not block the close).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Final

from pydantic import Field

from rulecam.ask.watch import FileAttacher
from rulecam.clients.errors import ModelClientError
from rulecam.clients.open_weights.glimmer import user_message
from rulecam.contracts import Frozen, StructuredLLM
from rulecam.log import get_logger
from rulecam.retail.checks import DATA_NOT_INSTRUCTIONS, GLIMMER_WAIT_S, SPARK_MODEL, Timer
from rulecam.retail.models import ModelCall

_log = get_logger(__name__)

PROMPTS: Final = {
    "shelf": (
        "You check a supermarket restock job. The first image is the shelf before, with each "
        "empty gap drawn as a numbered yellow box. The second image is a photo staff took after "
        "restocking (it may be taken from a different angle or closer). For each numbered gap, "
        "say whether product now fills it (filled = true). If the photo does not show that part "
        "of the shelf, it is not filled. "
    ),
    "hazard": (
        "You check that a supermarket floor hazard was made safe. The first image is the floor "
        "before, with each hazard (spill, broken glass, fallen stock, obstruction) drawn as a "
        "numbered yellow box. The second image is a photo staff took afterwards (it may be taken "
        "from another angle). For each numbered hazard, say whether it is gone or made safe "
        "(filled = true means cleared). If the photo does not show that spot, it is not "
        "cleared. "
    ),
}


class GapFill(Frozen):
    """One gap, before and after."""

    number: int = Field(ge=1, le=100)
    filled: bool
    note: str = Field(max_length=200)


class ProofJudgement(Frozen):
    """A model's reading of the before/after pair (structured output)."""

    gaps: list[GapFill] = Field(default_factory=list, max_length=40)
    all_filled: bool
    summary: str = Field(max_length=400)


class ProofResult(Frozen):
    """What both models said, and whether the task closes."""

    spark: ProofJudgement
    glimmer: ProofJudgement | None
    closed: bool
    calls: list[ModelCall]
    seconds: float


class ProofChecker:
    """Compares a restock task's before image with the staff's photo."""

    def __init__(
        self,
        *,
        spark: StructuredLLM,
        files: FileAttacher,
        glimmer: StructuredLLM | None,
        glimmer_model: str,
        model: str = SPARK_MODEL,
        glimmer_wait_s: float = GLIMMER_WAIT_S,
    ) -> None:
        """Create the checker."""
        self._spark = spark
        self._files = files
        self._glimmer = glimmer
        self._glimmer_model = glimmer_model
        self._model = model
        self._wait = glimmer_wait_s
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="proof")

    def check(self, before: Path, after: Path, kind: str = "shelf") -> ProofResult:
        """Judge whether every gap (or hazard) marked on `before` is fixed in `after`."""
        start = time.monotonic()
        timer = Timer()
        prompt = PROMPTS.get(kind, PROMPTS["shelf"]) + DATA_NOT_INSTRUCTIONS
        spark = timer.run(
            self._model,
            "gap filled?",
            lambda: self._spark.structured(
                model=self._model,
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Before, then after."},
                            self._files.attach(before),
                            self._files.attach(after),
                        ],
                    },
                ],
                schema=ProofJudgement,
            ),
        )
        glimmer = self._glimmer_look(prompt, before, after, timer)
        closed = spark.all_filled and (glimmer is None or glimmer.all_filled)
        return ProofResult(
            spark=spark,
            glimmer=glimmer,
            closed=closed,
            calls=timer.calls,
            seconds=round(time.monotonic() - start, 2),
        )

    def _glimmer_look(
        self, prompt: str, before: Path, after: Path, timer: Timer
    ) -> ProofJudgement | None:
        if self._glimmer is None:
            return None
        glimmer = self._glimmer

        def call() -> ProofJudgement:
            future = self._pool.submit(
                glimmer.structured,
                model=self._glimmer_model,
                messages=[user_message(prompt, [before, after])],
                schema=ProofJudgement,
            )
            try:
                return future.result(timeout=self._wait)
            except FutureTimeout as exc:
                msg = f"Glimmer did not answer within {self._wait:.0f} s"
                raise TimeoutError(msg) from exc

        try:
            return timer.run(self._glimmer_model, "second look", call)
        except (TimeoutError, ModelClientError, ValueError) as exc:
            _log.warning("proof_glimmer_failed", error=type(exc).__name__)
            return None
