"""Crowd density from the runtime's SAM 3.1 person tracks (festival crowds).

The track store holds every tracked person's foot point on the ground plane in metres
(`foot_m_x`, `foot_m_y`, from the camera's calibration). For each keyframe, the **peak local
density** is the most people inside any disc of radius `DISC_RADIUS_M` centred on a person,
divided by the disc's area: a crush builds in a few square metres long before the average over
the whole view moves, so the average would hide it.

Levels (persons per m², `CrowdLevels`, configurable) follow the figures crowd-safety guidance
commonly uses: about 2 per m² is where a moving crowd starts to slow and bunch, 4 where people
can no longer choose where they walk, and 5 or more where pressure can build through a dense
crowd. A breach is an **episode**: the level held for at least `min_seconds`, so one crowded
keyframe is not an alarm.

Two limits, said in every report rather than hidden:
- SAM misses people in dense crowds (occlusion), so a count, and so a density, is a lower bound:
  the real crowd is at least this dense;
- a camera without a ground calibration has no metric positions, so no density at all.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Final, Literal

from pydantic import Field

from rulecam.contracts import Frozen

DISC_RADIUS_M: Final = 1.0
DEFAULT_MIN_SECONDS: Final = 10.0
MAX_GAP_S: Final = 5.0  # a quieter stretch shorter than this does not end an episode

Level = Literal["normal", "busy", "crowded", "critical"]


class CrowdLevels(Frozen):
    """Persons per m² at which each level starts."""

    busy: float = Field(default=2.0, gt=0)
    crowded: float = Field(default=4.0, gt=0)
    critical: float = Field(default=5.0, gt=0)

    def level(self, density: float) -> Level:
        """The level a density is at."""
        if density >= self.critical:
            return "critical"
        if density >= self.crowded:
            return "crowded"
        if density >= self.busy:
            return "busy"
        return "normal"


class DensitySample(Frozen):
    """One keyframe of one camera."""

    ts: datetime
    people: int = Field(description="Tracked people with a metric position in this keyframe.")
    peak_density: float = Field(description="Persons per m² in the densest disc.")
    peak_at_m: tuple[float, float] | None = Field(
        default=None, description="Centre of the densest disc on the ground plane, metres."
    )
    level: Level


class Episode(Frozen):
    """A stretch where the density held at or above a level."""

    start: datetime
    end: datetime
    level: Level
    peak_density: float
    peak_ts: datetime
    peak_people: int

    @property
    def seconds(self) -> float:
        """How long it lasted."""
        return (self.end - self.start).total_seconds()


def peak_local_density(
    points: Sequence[tuple[float, float]], radius_m: float = DISC_RADIUS_M
) -> tuple[float, tuple[float, float] | None]:
    """The most people in a disc of `radius_m` around any one of them, per m², and its centre."""
    if not points:
        return 0.0, None
    r2 = radius_m * radius_m
    best, centre = 0, points[0]
    for cx, cy in points:
        inside = sum(1 for x, y in points if (x - cx) ** 2 + (y - cy) ** 2 <= r2)
        if inside > best:
            best, centre = inside, (cx, cy)
    return best / (math.pi * r2), centre


def sample(
    ts: datetime,
    points: Sequence[tuple[float, float]],
    levels: CrowdLevels,
    radius_m: float = DISC_RADIUS_M,
) -> DensitySample:
    """The density sample of one keyframe's metric foot points."""
    density, centre = peak_local_density(points, radius_m)
    return DensitySample(
        ts=ts,
        people=len(points),
        peak_density=round(density, 3),
        peak_at_m=(round(centre[0], 2), round(centre[1], 2)) if centre else None,
        level=levels.level(density),
    )


_RANK: Final[dict[Level, int]] = {"normal": 0, "busy": 1, "crowded": 2, "critical": 3}


def episodes(
    samples: Sequence[DensitySample],
    *,
    at_least: Level = "crowded",
    min_seconds: float = DEFAULT_MIN_SECONDS,
    max_gap_s: float = MAX_GAP_S,
) -> list[Episode]:
    """Stretches at or above `at_least` lasting `min_seconds` or more (time-ordered samples).

    A dip below the level shorter than `max_gap_s` does not end a stretch. Each episode's level
    is the highest one it reached.
    """
    floor = _RANK[at_least]
    out: list[Episode] = []
    run: list[DensitySample] = []

    def close() -> None:
        if run and (run[-1].ts - run[0].ts).total_seconds() >= min_seconds:
            peak = max(run, key=lambda s: s.peak_density)
            out.append(
                Episode(
                    start=run[0].ts,
                    end=run[-1].ts,
                    level=max((s.level for s in run), key=_RANK.__getitem__),
                    peak_density=peak.peak_density,
                    peak_ts=peak.ts,
                    peak_people=peak.people,
                )
            )

    for s in sorted(samples, key=lambda s: s.ts):
        if _RANK[s.level] < floor:
            continue
        if run and (s.ts - run[-1].ts).total_seconds() > max_gap_s:
            close()
            run = []
        run.append(s)
    close()
    return out
