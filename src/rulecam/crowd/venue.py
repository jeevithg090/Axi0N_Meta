"""The venue layout: named places (gates, routes, exits, holding areas) and how they connect.

The operator describes the venue once, in plain words and as a small graph (`PUT /crowd/venue`):
each place has a kind in their own words, the places it leads to, and, when a camera sees it, the
camera and zone. The warden gives Muse Spark the layout with the newest readings of every place a
camera sees, so its staff actions name real places ("close Gate B inbound, open Route C") and can
weigh one camera's crowd against another's free route. Nothing here decides an action.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from pydantic import Field

from rulecam.contracts import Frozen
from rulecam.crowd.scan import CrowdReading

MAX_PLACES: Final = 32


class Place(Frozen):
    """One named place of the venue."""

    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(
        default="", max_length=32, description="In the operator's words: gate, route, exit, stair."
    )
    leads_to: list[str] = Field(default_factory=list, max_length=12)
    camera_id: str | None = Field(default=None, max_length=64)
    zone: str | None = Field(
        default=None, max_length=64, description="The camera's zone that sees this place."
    )
    note: str = Field(
        default="", max_length=200, description="Width, capacity or state, e.g. '2 m wide'."
    )


class Venue(Frozen):
    """The venue's places and links, with the operator's description."""

    description: str = Field(default="", max_length=2000)
    places: list[Place] = Field(default_factory=list, max_length=MAX_PLACES)
    sample: bool = Field(
        default=False, description="True for a SAMPLE layout (demo data, not a real venue)."
    )


def place_readings(
    venue: Venue, latest: Callable[[str], CrowdReading | None]
) -> list[dict[str, object]]:
    """Each place with the newest reading of the zone that sees it (for Spark and the page).

    Args:
        venue: The layout.
        latest: Camera id -> its newest reading.

    Returns:
        One row per place: its layout fields, and `now` with people, density, speed and
        coherence when a camera sees it.
    """
    rows: list[dict[str, object]] = []
    for place in venue.places:
        row: dict[str, object] = place.model_dump(exclude_defaults=True)
        reading = latest(place.camera_id) if place.camera_id else None
        zone = reading.zone(place.zone or "view") if reading is not None else None
        if reading is not None and zone is not None:
            row["now"] = {
                "at": reading.at.isoformat(),
                "people": zone.people,
                "density_ppm2": zone.density_ppm2,
                "speed_mps": zone.speed_mps,
                "coherence": zone.coherence,
                "counterflow_share": zone.counterflow_share,
            }
        rows.append(row)
    return rows


__all__ = ["Place", "Venue", "place_readings"]
