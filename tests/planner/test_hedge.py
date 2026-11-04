"""A hung SAM call gets a twin; the first answer wins; errors still surface."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from rulecam.contracts.core import Detection
from rulecam.planner.hedge import HedgedSegmenter


class Sam:
    """The first call hangs until released (or fails); later calls answer at once."""

    def __init__(self, *, first_fails: bool = False, all_fail: bool = False) -> None:
        self.calls = 0
        self.release = threading.Event()
        self.first_fails = first_fails
        self.all_fail = all_fail
        self._lock = threading.Lock()

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        with self._lock:
            self.calls += 1
            n = self.calls
        if self.all_fail:
            raise RuntimeError(f"down {n}")
        if n == 1:
            if self.first_fails:
                raise RuntimeError("first failed")
            self.release.wait(5)
        return []


def test_a_slow_call_is_hedged_and_the_twin_answers(tmp_path: Path) -> None:
    sam = Sam()
    hedged = HedgedSegmenter(sam, after_s=0.05)
    assert hedged.segment_image(tmp_path / "f.jpg", "person") == []
    assert sam.calls == 2
    sam.release.set()


def test_a_fast_answer_is_not_hedged_and_errors_surface(tmp_path: Path) -> None:
    fast = Sam(first_fails=True)
    with pytest.raises(RuntimeError, match="first failed"):
        HedgedSegmenter(fast, after_s=5).segment_image(tmp_path / "f.jpg", "person")
    assert fast.calls == 1
    broken = Sam(all_fail=True)
    with pytest.raises(RuntimeError, match="down"):
        HedgedSegmenter(broken, after_s=5).segment_image(tmp_path / "f.jpg", "person")
