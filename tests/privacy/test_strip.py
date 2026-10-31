from __future__ import annotations

import itertools
from pathlib import Path

import cv2
import numpy as np
import pytest

from rulecam.contracts import PrivacyMode
from rulecam.privacy import BackgroundPlate, FrameBundle, render_strip, strip_times
from tests.fakes import EPOCH
from tests.privacy.helpers import (
    BACKGROUND,
    HEIGHT,
    SENTINEL,
    WIDTH,
    SentinelSource,
    event,
    scene,
)


def plate() -> BackgroundPlate:
    empty = BackgroundPlate((HEIGHT, WIDTH))
    empty.seed_empty(scene())
    return empty


def load(path: str) -> np.ndarray:
    image = cv2.imread(path)
    assert image is not None
    return image


def sentinel_pixels(path: str) -> int:
    image = load(path)
    return int((image == np.array(SENTINEL, dtype=np.uint8)).all(axis=-1).sum())


def test_strip_times_are_evenly_spaced_including_both_ends() -> None:
    times = strip_times(EPOCH, EPOCH.replace(second=EPOCH.second + 5))
    assert len(times) == 6
    assert times[0] == EPOCH
    assert (times[-1] - times[0]).total_seconds() == 5.0
    gaps = {(b - a).total_seconds() for a, b in itertools.pairwise(times)}
    assert gaps == {1.0}
    assert strip_times(EPOCH, EPOCH, frames=1) == [EPOCH]
    with pytest.raises(ValueError, match="frames"):
        strip_times(EPOCH, EPOCH, frames=0)


@pytest.mark.parametrize("mode", [PrivacyMode.ANONYMOUS, PrivacyMode.STRICT])
def test_anonymous_strips_contain_no_raw_person_pixels(mode: PrivacyMode, tmp_path: Path) -> None:
    # Guardrail: the raw frame has a sentinel-coloured person; no output may contain it.
    paths = render_strip(event(), mode, SentinelSource(), plate(), tmp_path)
    assert len(paths) == 6
    assert all(sentinel_pixels(p) == 0 for p in paths)
    assert all(Path(p).parent == tmp_path for p in paths)
    assert {p.name for p in tmp_path.iterdir()} == {f"ev-1_{i}.png" for i in range(6)}


def test_anonymous_strips_show_the_background_with_a_skeleton(tmp_path: Path) -> None:
    (first, *_) = render_strip(event(), PrivacyMode.ANONYMOUS, SentinelSource(), plate(), tmp_path)
    image = load(first)
    assert tuple(image[10, 10]) == BACKGROUND
    assert (image != np.array(BACKGROUND, dtype=np.uint8)).any(axis=-1).sum() > 100


def test_standard_strips_show_the_scene_with_overlays_and_blurred_faces(tmp_path: Path) -> None:
    (first, *_) = render_strip(event(), PrivacyMode.STANDARD, SentinelSource(), plate(), tmp_path)
    image = load(first)
    body = image[200, 100].astype(int)  # a person pixel, now tinted
    assert tuple(body) != SENTINEL
    assert abs(int(body[1]) - SENTINEL[1]) > 20  # the tint raised green from 0
    assert tuple(image[10, 10]) == BACKGROUND


def test_one_frame_per_moment_is_requested_from_the_event_camera(tmp_path: Path) -> None:
    source = SentinelSource()
    render_strip(event(seconds=10), PrivacyMode.ANONYMOUS, source, plate(), tmp_path, frames=3)
    assert len(source.asked) == 3
    assert (source.asked[-1] - source.asked[0]).total_seconds() == 10.0


class MissingSource:
    def bundle(self, camera_id: str, ts: object) -> FrameBundle | None:
        return None


def test_moments_the_source_cannot_give_are_skipped(tmp_path: Path) -> None:
    paths = render_strip(event(), PrivacyMode.ANONYMOUS, MissingSource(), plate(), tmp_path)
    assert paths == ()


def test_an_unwritable_destination_raises(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("x")
    with pytest.raises(OSError, match=r"Not a directory"):
        render_strip(event(), PrivacyMode.ANONYMOUS, SentinelSource(), plate(), blocked / "sub")


def test_a_failed_write_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cv2, "imwrite", lambda *_: False)
    with pytest.raises(OSError, match="could not write"):
        render_strip(event(), PrivacyMode.ANONYMOUS, SentinelSource(), plate(), tmp_path)
