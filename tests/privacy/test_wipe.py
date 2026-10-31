from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from rulecam.privacy import ConsentError, wipe_frame, write_wipe_video
from rulecam.privacy.wipe import BAR_COLOUR, BAR_WIDTH

RAW = np.full((40, 100, 3), 200, dtype=np.uint8)
ANON = np.full((40, 100, 3), 10, dtype=np.uint8)


def test_the_bar_sweeps_from_raw_to_skeleton() -> None:
    start = wipe_frame(RAW, ANON, 0.0)
    middle = wipe_frame(RAW, ANON, 0.5)
    end = wipe_frame(RAW, ANON, 1.0)
    assert (start[:, BAR_WIDTH:] == 200).all()
    assert (middle[:, :40] == 10).all()
    assert (middle[:, 60:] == 200).all()
    assert tuple(middle[0, 50]) == BAR_COLOUR
    assert (end[:, : 100 - BAR_WIDTH] == 10).all()


def test_the_inputs_are_not_modified() -> None:
    raw, anon = RAW.copy(), ANON.copy()
    wipe_frame(raw, anon, 0.5)
    assert (raw == RAW).all()
    assert (anon == ANON).all()


def test_bad_arguments_are_refused() -> None:
    with pytest.raises(ValueError, match="same shape"):
        wipe_frame(RAW, ANON[:, :50], 0.5)
    with pytest.raises(ValueError, match="between 0 and 1"):
        wipe_frame(RAW, ANON, 1.5)


def test_the_video_needs_confirmed_consent(tmp_path: Path) -> None:
    path = tmp_path / "wipe.mp4"
    with pytest.raises(ConsentError, match="consent"):
        write_wipe_video([RAW], [ANON], path, consent_confirmed=False)
    assert not path.exists()


def test_the_video_is_written_with_every_frame(tmp_path: Path) -> None:
    path = tmp_path / "wipe.mp4"
    frames = [RAW] * 8
    written = write_wipe_video(frames, [ANON] * 8, path, consent_confirmed=True, fps=8.0)
    assert written == path
    capture = cv2.VideoCapture(str(path))
    assert capture.isOpened()
    assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 8
    capture.release()


def test_frame_counts_must_match_and_not_be_empty(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-zero"):
        write_wipe_video([RAW], [], tmp_path / "a.mp4", consent_confirmed=True)
    with pytest.raises(ValueError, match="non-zero"):
        write_wipe_video([], [], tmp_path / "b.mp4", consent_confirmed=True)


def test_an_unopenable_destination_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="could not open"):
        write_wipe_video([RAW], [ANON], tmp_path / "missing" / "w.mp4", consent_confirmed=True)
