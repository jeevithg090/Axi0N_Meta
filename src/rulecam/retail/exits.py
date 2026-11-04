"""The "fire exit blocked for more than N minutes" rule, typed.

Blocked fire exits are a classic retail citation (and a fire risk) and the rule is about time:
a trolley parked in front of the door for a minute is not the problem, an exit that stays blocked
for five is. So the rule has two parts:

- **Is it blocked now?** Muse Spark 1.3 looks at the camera frame and judges whether the exit
  door and the floor in front of it are clear, naming what blocks it with a phrase SAM 3.1 can
  find; SAM must find that obstruction inside Spark's box (a second model, as for hazards).
- **For how long?** Code counts back over this camera's earlier exit readings while they were
  blocked: the exit has been blocked since the first of that unbroken run. The owner's limit
  (`ExitRule.max_blocked_s`, five minutes by default) is the rule; staff are alerted when the
  measured time reaches it, with the marked photo, and a hazard task opens (closed by a photo
  or a clear re-check like any hazard, so the chain keeps "seen at, cleared at").
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import Field

from rulecam.ask.vision import marked
from rulecam.ask.watch import FileAttacher
from rulecam.clients.errors import ModelClientError
from rulecam.contracts import Clock, Frozen, StructuredLLM
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter
from rulecam.retail.checks import (
    DATA_NOT_INSTRUCTIONS,
    OVERLAP,
    SAM_MODEL,
    SPARK_MODEL,
    Frames,
    History,
    RetailError,
    Site,
    Timer,
    image_size,
    new_id,
    overlap_share,
)
from rulecam.retail.models import BoxGrid, Evidence, Finding, Watch
from rulecam.shelf.gaps import clamp, to_pixels

_log = get_logger(__name__)

DEFAULT_MAX_BLOCKED_S: Final = 300.0


class ExitRule(Frozen):
    """The typed rule: alert when the exit has been blocked for `max_blocked_s` or longer."""

    max_blocked_s: float = Field(default=DEFAULT_MAX_BLOCKED_S, ge=0, le=86400)


class ExitJudgement(Frozen):
    """Muse Spark's reading of a fire-exit camera (structured output)."""

    exit_visible: bool = Field(description="A fire or emergency exit is in view.")
    blocked: bool = Field(description="Something stands in the exit or the floor in front of it.")
    what: str = Field(default="", max_length=200, description="What blocks it, or ''.")
    box: BoxGrid | None = Field(
        default=None, description="[x1, y1, x2, y2] of the obstruction on a 0-1000 grid."
    )
    sam_phrase: str = Field(default="", max_length=80, description="A noun phrase for SAM 3.1.")
    staff_message: str = Field(default="", max_length=300)


EXIT_PROMPT: Final = (
    "You check a supermarket fire exit for its owner. Find the emergency or fire exit door in "
    "the image (a green running-man sign, push bar, 'Fire exit' sign). Is the door or the floor "
    "in front of it blocked by anything: stock, boxes, pallets, trolleys, cleaning carts, "
    "displays? A person walking past does not block it. If it is blocked, give the obstruction's "
    "box as [x1, y1, x2, y2] on a 0-1000 grid, a short noun phrase SAM 3.1 can find it with, and "
    "a one-line message for staff. " + DATA_NOT_INSTRUCTIONS
)


def blocked_since(earlier: Sequence[Finding], now: datetime) -> datetime:
    """When the unbroken run of blocked readings that ends now began (`now` when none)."""
    since = now
    for f in earlier:  # newest first
        if f.detail.get("blocked") is not True:
            break
        since = f.at or f.checked_at
    return since


class ExitChecker:
    """Judges a fire-exit camera and measures how long the exit has been blocked."""

    def __init__(
        self,
        *,
        spark: StructuredLLM,
        files: FileAttacher,
        sam: ImageSegmenter,
        frames: Frames,
        site: Site,
        clock: Clock,
        marks_dir: Path,
        history: History,
        rule: ExitRule | None = None,
        model: str = SPARK_MODEL,
    ) -> None:
        """Create the checker (see `QueueChecker` for the arguments)."""
        self._spark = spark
        self._files = files
        self._sam = sam
        self._frames = frames
        self._site = site
        self._clock = clock
        self._marks = marks_dir
        self._history = history
        self._rule = rule or ExitRule()
        self._model = model

    def check(self, camera_id: str, at: datetime, watch: Watch | None = None) -> Finding:
        """Check the exit on `camera_id` at `at`.

        Raises:
            RetailError: Without a recorded frame there.
        """
        found = self._frames.frames(camera_id, [at])
        if not found:
            msg = f"no recorded frame on {camera_id} at {at.isoformat()}"
            raise RetailError(msg)
        start = time.monotonic()
        timer = Timer()
        image = found[0].path
        width, height = image_size(image)
        judged = timer.run(
            self._model,
            "exit judge",
            lambda: self._spark.structured(
                model=self._model,
                messages=[
                    {"role": "system", "content": EXIT_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": self._site.intro(camera_id, watch.focus if watch else ""),
                            },
                            self._files.attach(image),
                        ],
                    },
                ],
                schema=ExitJudgement,
            ),
        )
        board = found[0]
        sam_found = 0
        if judged.blocked and judged.box is not None:
            box = clamp(to_pixels(judged.box, width, height), width, height)
            board = marked(found[0], [("1", "obstruction", box)], self._marks)
            if judged.sam_phrase:
                try:
                    hits = timer.run(
                        SAM_MODEL,
                        "obstruction",
                        lambda: self._sam.segment_image(
                            image, judged.sam_phrase, camera_id=camera_id
                        ),
                    )
                    sam_found = sum(
                        1 for d in hits if overlap_share(box, d.mask.box_xyxy) >= OVERLAP
                    )
                except ModelClientError as exc:
                    _log.warning("exit_sam_failed", error=type(exc).__name__)
        blocked = (
            judged.exit_visible and judged.blocked and (sam_found > 0 or not judged.sam_phrase)
        )
        earlier = self._history(camera_id, "exit")
        since = blocked_since(earlier, at) if blocked else at
        blocked_for = (at - since).total_seconds() if blocked else 0.0
        limit = watch.max_blocked_s if watch and watch.max_blocked_s is not None else None
        limit_s = limit if limit is not None else self._rule.max_blocked_s
        act = blocked and blocked_for >= limit_s
        minutes = round(blocked_for / 60, 1)
        headline = (
            f"Fire exit blocked for {minutes} min by {judged.what or 'something'}"
            if blocked
            else ("Fire exit clear" if judged.exit_visible else "No fire exit in view")
        )
        return Finding(
            id=new_id("f"),
            kind="exit",
            camera_id=camera_id,
            camera_name=self._site.name(camera_id),
            at=at,
            checked_at=self._clock.now(),
            watch_id=watch.id if watch else None,
            act=act,
            headline=headline,
            staff_message=judged.staff_message if act else "",
            detail={
                **judged.model_dump(mode="json"),
                "blocked": blocked,
                "sam_found": sam_found,
                "blocked_since": since.isoformat() if blocked else None,
                "blocked_for_s": round(blocked_for, 1),
                "limit_s": limit_s,
            },
            evidence=Evidence(frames=[image.name], marked=board.path.name),
            calls=timer.calls,
            seconds=round(time.monotonic() - start, 2),
            metric=round(blocked_for, 1),
        )
