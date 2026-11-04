"""Named areas of a camera's view for crowd readings (shared by `crowd` and `ask`)."""

from __future__ import annotations

from pydantic import Field

from rulecam.contracts import Frozen


class CrowdZone(Frozen):
    """A named area of a camera's view (a gate, a queue, a stairway), as an image polygon."""

    name: str = Field(min_length=1, max_length=64)
    polygon_px: list[tuple[float, float]] = Field(min_length=3, max_length=32)
