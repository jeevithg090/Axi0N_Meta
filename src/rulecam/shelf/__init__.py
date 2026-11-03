"""Supermarket shelf gaps: Muse Spark finds them, SAM 3.1 confirms them (`gaps.py`)."""

from rulecam.shelf.api import shelf_router
from rulecam.shelf.gaps import (
    GapDraft,
    RejectedGap,
    ShelfDraft,
    ShelfError,
    ShelfGap,
    ShelfReport,
    ShelfScanner,
    clamp,
    cover,
)

__all__ = [
    "GapDraft",
    "RejectedGap",
    "ShelfDraft",
    "ShelfError",
    "ShelfGap",
    "ShelfReport",
    "ShelfScanner",
    "clamp",
    "cover",
    "shelf_router",
]
