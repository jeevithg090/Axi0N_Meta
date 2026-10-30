"""Surprise Lens contracts (task book §4.6, JEPA §4)."""

from __future__ import annotations

from datetime import datetime

from rulecam.contracts.core import Frozen


class SurpriseWindow(Frozen):
    """The surprise score for one sliding window under one token-selection condition."""

    camera_id: str
    window_start: datetime
    score: float
    z: float
    tokens_kept: int
    tokens_total: int
    condition: str  # "A_full" | "B_sam" | "C_random" | "D_motion" | "E_framediff" | "F_vlm"


class SurpriseEvent(Frozen):
    """A triggered surprise with its heatmap and the SAM concepts it overlaps."""

    event_id: str
    camera_id: str
    windows: tuple[SurpriseWindow, ...]
    heatmap_path: str
    labels: tuple[str, ...]  # SAM concepts intersecting the hot region
    peak_xy: tuple[float, float]
