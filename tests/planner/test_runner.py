"""The planner runner behind `POST /plans/requests` and its chunk frame source (P2-06a)."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rulecam.api.app import build_planner
from rulecam.api.plans import PlannerRunner, PlanRegistry
from rulecam.bus import Bus
from rulecam.config import Settings
from rulecam.contracts import Chunk, RequestItem, SitePlan
from rulecam.planner.pipeline import PipelineInput
from rulecam.planner.runner import ChunkFrames, PlannerService, ffmpeg_last_frame, new_plan_id
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.factory import build_perception
from tests.fakes import EPOCH, FakeClock
from tests.planner.test_pipeline import HANDWASH, _pipeline, _spark


def test_submit_runs_the_pipeline_in_the_background_and_hands_over_the_plan(
    tmp_path: Path,
) -> None:
    frames = {"cam1": [tmp_path / f"f{i}.jpg" for i in range(24)]}
    finished: list[SitePlan] = []
    service = PlannerService(
        _pipeline(tmp_path, _spark()),
        site_name="Main St kitchen",
        frames=lambda: frames,
        on_plan=finished.append,
        roles={"shift_lead": "web_push"},
        new_id=lambda: "plan-abc",
    )
    runner: PlannerRunner = service

    plan_id = runner.submit([RequestItem(kind="text", text=HANDWASH)])
    service.shutdown()

    assert plan_id == "plan-abc"
    assert [p.plan_id for p in finished] == ["plan-abc"]
    saved = PipelineInput.model_validate_json(
        (tmp_path / "plans" / "plan-abc" / "input.json").read_text(encoding="utf-8")
    )
    assert saved.camera_frames == {"cam1": tuple(frames["cam1"])}
    assert saved.roles == {"shift_lead": "web_push"}


def test_a_failed_run_is_logged_and_does_not_stop_the_worker(tmp_path: Path) -> None:
    finished: list[SitePlan] = []
    ids = iter(["../bad", "plan-ok"])
    service = PlannerService(
        _pipeline(tmp_path, _spark()),
        site_name="s",
        frames=dict,
        on_plan=finished.append,
        new_id=lambda: next(ids),
    )
    service.submit([RequestItem(kind="text", text=HANDWASH)])
    service.submit([RequestItem(kind="text", text=HANDWASH)])
    service.shutdown()
    assert [p.plan_id for p in finished] == ["plan-ok"]


def test_plan_ids_are_fresh_and_safe() -> None:
    first, second = new_plan_id(), new_plan_id()
    assert first != second
    assert first.startswith("plan-")
    assert len(first) == len("plan-") + 12


class Chunks:
    """A `ChunkLookup` over a fixed list of chunks."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks

    def last_seen(self) -> dict[str, datetime]:
        return {c.camera_id: c.start_ts for c in self._chunks}

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        return [c for c in self._chunks if c.camera_id == camera_id and t0 <= c.start_ts <= t1]


def _chunk(camera_id: str, path: str, age_s: float) -> Chunk:
    return Chunk(
        chunk_id=f"{camera_id}-{path}",
        camera_id=camera_id,
        path=path,
        start_ts=EPOCH + timedelta(seconds=100 - age_s),
        fps=10.0,
        n_frames=20,
        width=64,
        height=48,
    )


def test_chunk_frames_take_one_frame_per_recent_chunk(tmp_path: Path) -> None:
    extracted: list[Path] = []

    def extract(chunk: Path, frame: Path) -> None:
        if chunk.name == "broken.mp4":
            raise subprocess.CalledProcessError(1, ["ffmpeg"])
        extracted.append(chunk)
        frame.write_bytes(b"\xff\xd8\xff")

    clock = FakeClock(EPOCH + timedelta(seconds=100))
    chunks = Chunks(
        [
            _chunk("../cam", "/v/a.mp4", age_s=10),
            _chunk("../cam", "/v/old.mp4", age_s=90),
            _chunk("../cam", "/v/broken.mp4", age_s=5),
            _chunk("cam2", "/v/broken.mp4", age_s=5),
        ]
    )
    source = ChunkFrames(chunks, clock=clock, out_dir=tmp_path / "frames", extract=extract)

    frames = source()
    assert list(frames) == ["../cam"]  # cam2 had no usable frame
    (frame,) = frames["../cam"]
    assert frame.parent == tmp_path / "frames"  # content-addressed, not named by the camera
    assert frame.suffix == ".jpg"
    assert sorted(p.name for p in (tmp_path / "frames").iterdir()) == [frame.name]

    assert source() == frames  # cached: not extracted again
    assert extracted == [Path("/v/a.mp4")]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_ffmpeg_last_frame_writes_a_jpeg(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    subprocess.run(  # noqa: S603 - test fixture
        [  # noqa: S607
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x48:rate=10:duration=1",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(clip),
        ],
        check=True,
    )
    frame = tmp_path / "last.jpg"
    ffmpeg_last_frame(clip, frame)
    assert frame.read_bytes().startswith(b"\xff\xd8\xff")


def _settings(tmp_path: Path, mode: str) -> Settings:
    return Settings(
        rulecam_mode=mode,
        rulecam_data_dir=tmp_path,
        meta_api_base_url=None,
        meta_api_key=None,
        _env_file=None,
    )


def test_build_planner_needs_clients(tmp_path: Path) -> None:
    clock = FakeClock()
    bus = Bus()
    for mode, expect in (("replay", True), ("live", False)):
        settings = _settings(tmp_path, mode)
        cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
        perception = build_perception(settings, cache=cache, clock=clock, bus=bus)
        planner = build_planner(
            settings,
            cache=cache,
            clock=clock,
            bus=bus,
            perception=perception,
            plans=PlanRegistry(),
            site="demo",
        )
        assert (planner is not None) is expect, mode
