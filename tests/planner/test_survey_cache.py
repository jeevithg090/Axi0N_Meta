"""Reusing a fresh survey of the same scene, and only then."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from rulecam.contracts.core import Calibration
from rulecam.contracts.plan import InventoryItem
from rulecam.planner.survey_cache import SurveyCache, hamming, scene_hash
from tests.fakes import FakeClock


def _scene(path: Path, seed: int, *, walker: int | None = None) -> Path:
    rng = np.random.default_rng(seed)
    image = cv2.resize(
        rng.integers(0, 255, (9, 16), dtype=np.uint8), (320, 180), interpolation=cv2.INTER_NEAREST
    )
    if walker is not None:  # someone passing through one frame
        image[60:140, walker : walker + 30] = 255
    cv2.imwrite(str(path), image)
    return path


ITEM = InventoryItem(concept="forklift", camera_id="cam1", count=1, mean_confidence=0.9)
CAL = Calibration(
    camera_id="cam1",
    homography=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    err_m=0.15,
    method="reference_object",
)


def test_same_scene_with_people_passing_hashes_alike(tmp_path: Path) -> None:
    frames = [_scene(tmp_path / f"{i}.jpg", 1, walker=40 * i) for i in range(5)]
    other = [_scene(tmp_path / f"o{i}.jpg", 2) for i in range(5)]
    base = scene_hash(frames)
    assert base is not None
    assert hamming(base, scene_hash(frames[1:]) or ()) <= 8
    assert hamming(base, scene_hash(other) or ()) > 64
    assert scene_hash([tmp_path / "missing.jpg"]) is None


def test_reuse_only_when_every_camera_is_fresh_and_unchanged(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = SurveyCache(clock=clock, max_age_s=600)
    scene = scene_hash([_scene(tmp_path / "a.jpg", 1)])
    moved = scene_hash([_scene(tmp_path / "b.jpg", 3)])
    assert cache.lookup({"cam1": scene}) is None
    cache.keep("plan-1", {"cam1": scene}, [ITEM], {"cam1": CAL})
    clock.advance(120)
    hit = cache.lookup({"cam1": scene})
    assert hit is not None
    inventory, calibrations, reused = hit
    assert inventory == (ITEM,)
    assert calibrations == {"cam1": CAL}
    assert (reused["cam1"].plan_id, reused["cam1"].age_s, reused["cam1"].bits) == ("plan-1", 120, 0)
    assert cache.lookup({"cam1": moved}) is None  # the camera moved
    assert cache.lookup({"cam1": scene, "cam2": scene}) is None  # cam2 never surveyed
    assert cache.lookup({"cam1": None}) is None
    clock.advance(600)
    assert cache.lookup({"cam1": scene}) is None  # too old
    off = SurveyCache(clock=clock, max_age_s=0)
    off.keep("plan-2", {"cam1": scene}, [ITEM], {})
    assert off.lookup({"cam1": scene}) is None
