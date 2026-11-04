"""Fakes for the supermarket tests: frames, SAM 3.1, file uploads, notifier."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from rulecam.ask.vision import Frame
from rulecam.contracts import Detection, Mask

AT = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
W, H = 400, 200


def image(path: Path, value: int = 200) -> Path:
    """A plain grey test image."""
    cv2.imwrite(str(path), np.full((H, W, 3), value, dtype=np.uint8))
    return path


def detection(concept: str, box: tuple[float, float, float, float]) -> Detection:
    """One SAM detection."""
    return Detection(
        camera_id="cam",
        frame_idx=0,
        ts=AT,
        concept=concept,
        object_id=1,
        mask=Mask(rle="", box_xyxy=box, confidence=0.9),
    )


class FakeFrames:
    """Recorded frames: the same image at every instant, for the cameras it knows."""

    def __init__(self, path: Path, cameras: Sequence[str] = ("till",)) -> None:
        self.path = path
        self.known = list(cameras)
        self.out_dir = path.parent

    def frames(self, camera_id: str, instants: Sequence[datetime]) -> list[Frame]:
        if camera_id not in self.known:
            return []
        return [Frame(camera_id=camera_id, at=at, path=self.path) for at in instants]

    def cameras(self) -> dict[str, tuple[datetime, datetime]]:
        return {c: (AT - timedelta(minutes=5), AT) for c in self.known}


class FakeSam:
    """SAM 3.1 on one image: the boxes queued per concept."""

    def __init__(self, boxes: dict[str, list[tuple[float, float, float, float]]]) -> None:
        self.boxes = boxes
        self.calls: list[str] = []

    def segment_image(
        self, frame: Path, concept: str, *, camera_id: str = "", include_confidence: bool = True
    ) -> list[Detection]:
        self.calls.append(concept)
        return [detection(concept, b) for b in self.boxes.get(concept, [])]


class Files:
    """Spark's file upload: a content part naming the file."""

    def attach(self, path: Path) -> dict[str, object]:
        return {"type": "input_image", "file_id": path.name}


class FakeNotifier:
    """Records alerts."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send(
        self,
        role: str,
        title: str,
        body: str,
        *,
        clip_path: str | None = None,
        buttons: tuple[str, ...] = (),
    ) -> str:
        self.sent.append(
            {"role": role, "title": title, "body": body, "clip": clip_path, "buttons": buttons}
        )
        return f"al-{len(self.sent)}"


@pytest.fixture
def frame(tmp_path: Path) -> Path:
    return image(tmp_path / "frame.jpg")
