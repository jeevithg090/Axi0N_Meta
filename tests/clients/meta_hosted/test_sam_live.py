"""Live checks against the real hosted SAM 3.1 API (`make live-test`; never run in CI).

They verify the request format and wire grammar in `sam_wire` (issue #5; last passed Oct 30 2026).
Run with `RULECAM_MODE=live META_API_KEY=… META_API_BASE_URL=https://api.meta.ai/v1`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from rulecam.clients.meta_hosted.sam import HostedSam31
from rulecam.clients.meta_hosted.sam_cost import MIN_BILLED_FRAMES
from rulecam.config import Settings
from rulecam.contracts import Chunk
from tests.fakes import EPOCH, FakeClock

pytestmark = pytest.mark.live

WIDTH, HEIGHT, FPS, SECONDS = 640, 360, 10, 3


def _live_client() -> HostedSam31:
    settings = Settings()
    if settings.rulecam_mode == "replay" or settings.meta_api_key is None:
        pytest.skip("needs RULECAM_MODE=live and META_API_KEY")
    return HostedSam31.from_settings(settings, clock=FakeClock())


def _testsrc2_chunk(tmp_path: Path) -> Chunk:
    """The same 640x360, 30-frame `testsrc2` clip the Oct 30 recordings were made from."""
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
            f"testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS}",
            "-t",
            str(SECONDS),
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
        fps=FPS,
        n_frames=FPS * SECONDS,
        width=WIDTH,
        height=HEIGHT,
    )


def test_live_concept_and_box_prompts(tmp_path: Path) -> None:
    client = _live_client()
    chunk = _testsrc2_chunk(tmp_path)

    rectangles = client.segment(chunk, "rectangle")
    first = rectangles[0] if rectangles else None
    boxed = (
        client.segment(chunk, "rectangle", box_prompts={1: first.mask.box_xyxy}) if first else []
    )

    assert len(rectangles) >= 1
    assert [d.frame_idx for d in rectangles] == sorted(d.frame_idx for d in rectangles)
    assert boxed
    assert {d.object_id for d in boxed} == {1}
    cost = client.costs.snapshot()["live"]
    assert cost.video_requests == 2
    assert cost.billed_frames == MIN_BILLED_FRAMES * cost.video_requests  # 30 frames bill as 50
