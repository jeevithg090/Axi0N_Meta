"""Shared helpers for the connector tests: a rendered test clip (no network)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def render_clip(path: Path, seconds: float, fps: int = 10) -> Path:
    """Render an ffmpeg `testsrc` clip."""
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x240:rate={fps}",
            "-t",
            f"{seconds}",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture(scope="session")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 3 s test clip."""
    return render_clip(tmp_path_factory.mktemp("clips") / "clip.mp4", 3.0)
