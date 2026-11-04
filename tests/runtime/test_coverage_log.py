"""`CoverageLog` chains one `coverage` record per confirmed BLIND / OK change (real MP4 chunks)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from rulecam.contracts import Chunk
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.runtime.coverage_log import CoverageLog
from tests.fakes import EPOCH, FakeClock

WIDTH, HEIGHT, FPS, FRAMES = 320, 180, 10.0, 10


def _scene() -> np.ndarray:
    rng = np.random.default_rng(0)
    coarse = rng.integers(40, 220, size=(HEIGHT // 10, WIDTH // 10), dtype=np.uint8)
    gray = cv2.resize(coarse, (WIDTH, HEIGHT), interpolation=cv2.INTER_NEAREST)
    return np.asarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), dtype=np.uint8)


def _write(path: Path, dark: bool, seed: int) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT))
    rng = np.random.default_rng(seed)
    base = np.full((HEIGHT, WIDTH, 3), 4, np.uint8) if dark else _scene()
    for _ in range(FRAMES):
        noise = rng.normal(0.0, 6.0, base.shape)
        writer.write(np.clip(base.astype(np.float32) + noise, 0, 255).astype(np.uint8))
    writer.release()


class Chunks:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.known = chunks

    def last_seen(self) -> dict[str, datetime]:
        return {"cam1": self.known[-1].start_ts} if self.known else {}

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        return [c for c in self.known if t0 <= c.start_ts <= t1]


def _chunk(tmp_path: Path, index: int, *, dark: bool) -> Chunk:
    path = tmp_path / f"c{index}.mp4"
    _write(path, dark, index)
    return Chunk(
        chunk_id=f"cam1_{index:06d}",
        camera_id="cam1",
        path=str(path),
        start_ts=EPOCH + timedelta(seconds=index * FRAMES / FPS),
        fps=FPS,
        n_frames=FRAMES,
        width=WIDTH,
        height=HEIGHT,
    )


def test_dark_spell_is_chained_once_going_blind_and_once_coming_back(tmp_path: Path) -> None:
    pattern = [False, False, True, True, True, False, False, False]
    lister = Chunks([])
    chain = EventChain(tmp_path / "chain", "demo", clock=FakeClock(EPOCH))
    log = CoverageLog(lister, chain, sleep=lambda _: None)
    for index, dark in enumerate(pattern):
        lister.known.append(_chunk(tmp_path, index, dark=dark))
        log.poll()
    log.poll()  # nothing new: nothing observed twice
    records = read_chain(chain.path_for(EPOCH.date()))
    states = [(r.payload["state"], r.payload["reason"]) for r in records]
    assert states == [("blind", "dark"), ("ok", None)]
    assert {r.kind for r in records} == {"coverage"}
    assert records[0].payload["camera_id"] == "cam1"


def test_unreadable_chunk_is_skipped(tmp_path: Path) -> None:
    chain = EventChain(tmp_path / "chain", "demo", clock=FakeClock(EPOCH))
    log = CoverageLog(Chunks([]), chain, sleep=lambda _: None)
    broken = _chunk(tmp_path, 0, dark=False).model_copy(update={"path": str(tmp_path / "x.mp4")})
    assert log.observe(broken) is None
    with pytest.raises(FileNotFoundError):
        read_chain(chain.path_for(EPOCH.date()))
