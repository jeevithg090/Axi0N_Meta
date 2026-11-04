"""Reuse a recent survey of the same camera looking at the same scene.

The survey (what a camera can see, and its ground calibration) is the planner's slowest step:
Spark proposes up to 30 things and SAM 3.1 looks for each on 8 frames. When the owner plans a
second rule on a camera minutes after the first, the scene has not changed, and that work would
be repeated for nothing. `SurveyCache` keeps each camera's last survey with a perceptual hash of
its scene: the median of the sampled frames (people and vehicles passing through drop out of a
median), grey, 16 x 16, one bit per pixel against the mean. A new survey of the camera reuses the
kept one when every camera of the request is kept, the kept survey is younger than `max_age_s`
and the scene hash differs in at most `max_bits` of 256 bits (lighting, a vehicle that stays
long in view; not a moved camera). The plan says what it reused and how old it was.

Nothing else is reused: the probe still measures every needed concept on the current frames,
the calibration is still checked against the people seen now, and Rule CI runs in full.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Final

import cv2
import numpy as np

from rulecam.contracts import Clock, Frozen
from rulecam.contracts.core import Calibration
from rulecam.contracts.plan import InventoryItem
from rulecam.log import get_logger

_log = get_logger(__name__)

HASH_SIDE: Final = 16
DEFAULT_MAX_AGE_S: Final = 1800.0
# Of 256 bits (19%): a looping clip with a large moving forklift differed by 30-39 bits between
# plans on 4 Nov; another scene differs by over 100.
DEFAULT_MAX_BITS: Final = 48


class Reused(Frozen):
    """Where a camera's survey came from when it was reused.

    Attributes:
        plan_id: The plan whose survey it was.
        age_s: How old it was when reused.
        bits: How many of the 256 scene-hash bits differed.
    """

    plan_id: str
    age_s: float
    bits: int


class _Entry(Frozen):
    plan_id: str
    scene: tuple[bool, ...]
    inventory: tuple[InventoryItem, ...]
    calibration: Calibration | None
    at: datetime


def scene_hash(frames: Sequence[Path]) -> tuple[bool, ...] | None:
    """The 256-bit average hash of the median of `frames`, or `None` when none can be read."""
    grays = []
    for frame in frames:
        image = cv2.imread(str(frame), cv2.IMREAD_GRAYSCALE)
        if image is not None:
            grays.append(cv2.resize(image, (HASH_SIDE, HASH_SIDE), interpolation=cv2.INTER_AREA))
    if not grays:
        return None
    median = np.median(np.stack(grays).astype(np.float32), axis=0)
    return tuple(bool(v) for v in (median > median.mean()).flatten())


def hamming(a: Sequence[bool], b: Sequence[bool]) -> int:
    """Bits that differ between two hashes of the same length."""
    return sum(x != y for x, y in zip(a, b, strict=True))


class SurveyCache:
    """Each camera's last survey, kept in memory for the API's lifetime (thread-safe)."""

    def __init__(
        self,
        *,
        clock: Clock,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        max_bits: int = DEFAULT_MAX_BITS,
    ) -> None:
        """Create an empty cache.

        Args:
            clock: Time source.
            max_age_s: The oldest survey still reused (0 turns reuse off).
            max_bits: The most scene-hash bits that may differ.
        """
        self._clock = clock
        self._max_age_s = max_age_s
        self._max_bits = max_bits
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def lookup(
        self, scenes: Mapping[str, tuple[bool, ...] | None]
    ) -> tuple[tuple[InventoryItem, ...], dict[str, Calibration], dict[str, Reused]] | None:
        """The kept surveys of every camera, if all are fresh and show the same scene.

        Args:
            scenes: Camera id -> the scene hash of its current frames.

        Returns:
            The inventory, calibrations and what was reused, or `None` when any camera misses.
        """
        if self._max_age_s <= 0 or not scenes:
            return None
        now = self._clock.now()
        inventory: list[InventoryItem] = []
        calibrations: dict[str, Calibration] = {}
        reused: dict[str, Reused] = {}
        with self._lock:
            for camera_id, scene in sorted(scenes.items()):
                entry = self._entries.get(camera_id)
                if entry is None or scene is None:
                    return None
                age = (now - entry.at).total_seconds()
                bits = hamming(entry.scene, scene)
                if age > self._max_age_s or bits > self._max_bits:
                    _log.info("survey_cache_miss", camera_id=camera_id, age_s=age, bits=bits)
                    return None
                inventory.extend(entry.inventory)
                if entry.calibration is not None:
                    calibrations[camera_id] = entry.calibration
                reused[camera_id] = Reused(plan_id=entry.plan_id, age_s=round(age, 1), bits=bits)
        return tuple(inventory), calibrations, reused

    def keep(
        self,
        plan_id: str,
        scenes: Mapping[str, tuple[bool, ...] | None],
        inventory: Sequence[InventoryItem],
        calibrations: Mapping[str, Calibration],
    ) -> None:
        """Keep a fresh survey's result per camera (cameras without a scene hash are skipped)."""
        now = self._clock.now()
        with self._lock:
            for camera_id, scene in scenes.items():
                if scene is None:
                    continue
                self._entries[camera_id] = _Entry(
                    plan_id=plan_id,
                    scene=scene,
                    inventory=tuple(i for i in inventory if i.camera_id == camera_id),
                    calibration=calibrations.get(camera_id),
                    at=now,
                )
