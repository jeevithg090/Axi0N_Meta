"""The shared check format: what a model-written `check(sample)` receives and must return.

A check is called once per timeline sample with `sample` as a plain dict holding exactly the
fields of `CheckSample` (`CheckSample.as_json`). It must return a dict that validates as
`CheckResult`. These names are a plug-in point: the sandbox, the proof steps, the generator's
prompt and every add-on code against them, so change them only together.

Privacy (AGENTS.md rule 7): a sample has no track ids, no identities and nothing per person over
time; it is one instant of one camera, objects by concept only.

`mask_top_y_px` and `mask_area_px` come from the object's box today (SAM masks are not stored on
tracks), so they are the box's top edge and the box's area; the names stay so mask-backed values
can replace them without changing any check.
"""

from __future__ import annotations

import math
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_WHY_CHARS: Final = 120

PointXY = tuple[float, float]


class CheckObject(BaseModel):
    """One detected object at the sample's instant.

    Attributes:
        concept: The object's concept, e.g. "pallet".
        box_xyxy_px: Its box in image pixels, `[x1, y1, x2, y2]` (y grows downwards).
        mask_top_y_px: The object's highest image row (box top until masks are stored).
        mask_area_px: Its area in square pixels (box area until masks are stored).
        ground_xy_m: Its foot point on the ground in metres, when the camera is calibrated.
        score: Detection confidence in `[0, 1]`, when known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: str
    box_xyxy_px: tuple[float, float, float, float]
    mask_top_y_px: float
    mask_area_px: float = Field(ge=0)
    ground_xy_m: PointXY | None = None
    score: float | None = Field(default=None, ge=0, le=1)


class CheckSample(BaseModel):
    """One timeline sample of one camera, as the check sees it.

    Attributes:
        timestamp: The sample's time, ISO 8601 in UTC.
        frame_w: Frame width in pixels, when known.
        frame_h: Frame height in pixels, when known.
        objects: Objects present at this instant (the rule's concept only).
        zones: The camera's polygon zones, `{name: [[x, y], ...]}` in pixels.
        lines: Owner-drawn reference lines, `{name: [[x, y], [x, y]]}` in pixels.
        px_per_m: Image pixels per ground metre near the scene's centre, when calibrated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: str
    frame_w: int | None = Field(default=None, gt=0)
    frame_h: int | None = Field(default=None, gt=0)
    objects: tuple[CheckObject, ...] = ()
    zones: dict[str, tuple[PointXY, ...]] = Field(default_factory=dict)
    lines: dict[str, tuple[PointXY, PointXY]] = Field(default_factory=dict)
    px_per_m: float | None = Field(default=None, gt=0)

    def as_json(self) -> dict[str, object]:
        """The plain dict a check receives (lists, not tuples; every key present)."""
        return self.model_dump(mode="json")


class CheckResult(BaseModel):
    """What `check(sample)` must return for one sample.

    Attributes:
        value: True when the owner's condition holds (a violation), else False.
        measure: An optional finite number behind the decision (pixels, metres, a count).
        why: A short reason in plain words (at most `MAX_WHY_CHARS`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    value: bool
    measure: float | None = None
    why: str = Field(default="", max_length=MAX_WHY_CHARS)

    @field_validator("measure", mode="before")
    @classmethod
    def _finite(cls, raw: object) -> object:
        if isinstance(raw, bool):
            msg = "measure must be a number, not a bool"
            raise ValueError(msg)  # noqa: TRY004 - pydantic turns only ValueError into a field error
        if isinstance(raw, int):
            raw = float(raw)
        if isinstance(raw, float) and not math.isfinite(raw):
            msg = "measure must be finite"
            raise ValueError(msg)
        return raw
