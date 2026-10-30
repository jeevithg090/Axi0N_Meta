"""Live checks against the real hosted SAM 3.1 API (`make live-test`; never run in CI).

They verify the request format and wire grammar assumed in `sam_wire` (see the P1-02 issue).
Run with `RULECAM_MODE=live META_API_KEY=… META_API_BASE_URL=https://api.meta.ai/v1`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from rulecam.clients.meta_hosted.sam import HostedSam31
from rulecam.config import Settings
from rulecam.contracts import Chunk
from tests.fakes import EPOCH, FakeClock

pytestmark = pytest.mark.live


def _live_client() -> HostedSam31:
    settings = Settings()
    if settings.rulecam_mode == "replay" or settings.meta_api_key is None:
        pytest.skip("needs RULECAM_MODE=live and META_API_KEY")
    return HostedSam31.from_settings(settings, clock=FakeClock())


def _testsrc_chunk(tmp_path: Path) -> Chunk:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("needs ffmpeg")
    path = tmp_path / "live_000000.mp4"
    subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [
            ffmpeg,
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return Chunk(
        chunk_id="live_000000",
        camera_id="live",
        path=str(path),
        start_ts=EPOCH,
        fps=10,
        n_frames=30,
        width=320,
        height=240,
    )


def test_live_concept_and_box_prompts_are_accepted(tmp_path: Path) -> None:
    client = _live_client()
    chunk = _testsrc_chunk(tmp_path)

    concept = client.segment(chunk, "circle")
    boxed = client.segment(chunk, "circle", box_prompts={1: (100, 60, 220, 180)})

    assert [d.frame_idx for d in concept] == sorted(d.frame_idx for d in concept)
    assert {d.object_id for d in boxed} <= {1}
    assert client.costs.snapshot()["live"].billed_frames >= 100
