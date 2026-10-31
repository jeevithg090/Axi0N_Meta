"""The planner runner behind `POST /plans/requests` and its chunk frame source (P2-06a)."""

from __future__ import annotations

import itertools
import math
import shutil
import subprocess
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rulecam.api.app import build_planner
from rulecam.api.plans import PlannerRunner, PlanRegistry
from rulecam.bus import Bus
from rulecam.config import Settings
from rulecam.contracts import Chunk, RequestItem, SitePlan
from rulecam.planner import runner as runner_module
from rulecam.planner.feasibility import OBSERVABLE_LB
from rulecam.planner.pipeline import PipelineInput
from rulecam.planner.runner import (
    MIN_FRAME_SPACING_S,
    ChunkFrames,
    PlannerService,
    ffmpeg_frames,
    frame_indices,
    new_plan_id,
)
from rulecam.planner.site_config import SiteConfig
from rulecam.planner.steps.probe import DEFAULT_PROBE_FRAMES, probe_concept_on_camera
from rulecam.planner.steps.survey import frame_group
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.factory import build_perception
from tests.fakes import EPOCH, FakeClock
from tests.planner.test_pipeline import HANDWASH, _pipeline, _spark
from tests.planner.test_survey_probes import FrameSegmenter


def test_submit_runs_the_pipeline_in_the_background_and_hands_over_the_plan(
    tmp_path: Path,
) -> None:
    frames = {"cam1": [tmp_path / f"f{i}.jpg" for i in range(24)]}
    finished: list[SitePlan] = []
    service = PlannerService(
        _pipeline(tmp_path, _spark()),
        site=SiteConfig(site_name="Main St kitchen", roles={"shift_lead": "web_push"}),
        frames=lambda: frames,
        on_plan=finished.append,
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
        site=SiteConfig(site_name="s"),
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


def _chunk(
    camera_id: str, path: str, age_s: float, *, fps: float = 10.0, n_frames: int = 30
) -> Chunk:
    return Chunk(
        chunk_id=f"{camera_id}-{path}",
        camera_id=camera_id,
        path=path,
        start_ts=EPOCH + timedelta(seconds=100 - age_s),
        fps=fps,
        n_frames=n_frames,
        width=64,
        height=48,
    )


class FakeExtract:
    """Writes `1.jpg`, `2.jpg`, ... for the asked indices and records each call (one per chunk)."""

    def __init__(self, broken: frozenset[str] = frozenset(), short: frozenset[str] = frozenset()):
        self.calls: list[tuple[Path, tuple[int, ...]]] = []
        self._broken = broken
        self._short = short

    def __call__(self, chunk: Path, indices: Sequence[int], out_dir: Path) -> None:
        if chunk.name in self._broken:
            raise subprocess.CalledProcessError(1, ["ffmpeg"])
        self.calls.append((chunk, tuple(indices)))
        written = indices[:1] if chunk.name in self._short else indices
        for k, index in enumerate(written, start=1):
            (out_dir / f"{k}.jpg").write_bytes(b"\xff\xd8\xff" + str(index).encode())


@pytest.mark.parametrize(
    ("fps", "n_frames", "expected"),
    [
        (10.0, 30, (5, 15, 25)),  # 3 s chunk: 0.5 s, 1.5 s, 2.5 s
        (10.0, 20, (5, 15)),  # 2 s: only 2 fit 1 s apart
        (10.0, 5, (2,)),  # 0.5 s: one frame
        (10.0, 100, (16, 50, 83)),  # 10 s: capped at 3
        (1.0, 1, (0,)),
    ],
)
def test_frame_indices_are_evenly_spaced_at_least_a_second_apart(
    fps: float, n_frames: int, expected: tuple[int, ...]
) -> None:
    indices = frame_indices(_chunk("cam", "/v/a.mp4", 0, fps=fps, n_frames=n_frames))
    assert indices == expected
    assert all((b - a) / fps >= MIN_FRAME_SPACING_S for a, b in itertools.pairwise(indices))


def test_chunk_frames_take_several_frames_per_recent_chunk_in_one_call(tmp_path: Path) -> None:
    extract = FakeExtract(broken=frozenset({"broken.mp4"}))
    clock = FakeClock(EPOCH + timedelta(seconds=100))
    chunks = Chunks(
        [
            _chunk("../cam", "/v/b.mp4", age_s=7),
            _chunk("../cam", "/v/a.mp4", age_s=10),
            _chunk("../cam", "/v/old.mp4", age_s=90),
            _chunk("../cam", "/v/broken.mp4", age_s=5),
            _chunk("cam2", "/v/broken.mp4", age_s=5),
        ]
    )
    source = ChunkFrames(chunks, clock=clock, out_dir=tmp_path / "frames", extract=extract)

    frames = source()
    assert list(frames) == ["../cam"]  # cam2 had no usable frame
    cam = frames["../cam"]
    assert len(cam) == 6  # 3 per 3 s chunk, the 90 s old chunk is outside the window
    assert all(f.parent == tmp_path / "frames" and f.suffix == ".jpg" for f in cam)
    assert sorted(p.name for p in (tmp_path / "frames").iterdir()) == sorted(f.name for f in cam)
    # order: chunks as the index gives them, frames of a chunk in time order
    assert [f.read_bytes()[3:] for f in cam] == [b"5", b"15", b"25"] * 2
    assert {frame_group(f) for f in cam[:3]} != {frame_group(f) for f in cam[3:]}
    assert len({frame_group(f) for f in cam[:3]}) == 1

    assert source() == frames  # cached: not extracted again
    assert extract.calls == [(Path("/v/b.mp4"), (5, 15, 25)), (Path("/v/a.mp4"), (5, 15, 25))]


def test_chunk_frames_window_is_configurable(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH + timedelta(seconds=100))
    chunks = Chunks([_chunk("cam", f"/v/{age}.mp4", age_s=age) for age in (5, 50, 90)])
    out = tmp_path / "frames"

    short = ChunkFrames(chunks, clock=clock, out_dir=out, extract=FakeExtract(), window_s=30)
    long = ChunkFrames(chunks, clock=clock, out_dir=out, extract=FakeExtract(), window_s=120)

    assert len(short()["cam"]) == 3
    assert len(long()["cam"]) == 9


def test_chunk_frames_skip_a_chunk_ffmpeg_only_partly_extracted(tmp_path: Path) -> None:
    clock = FakeClock(EPOCH + timedelta(seconds=100))
    chunks = Chunks([_chunk("cam", "/v/short.mp4", age_s=5), _chunk("cam", "/v/ok.mp4", age_s=3)])
    extract = FakeExtract(short=frozenset({"short.mp4"}))
    out = tmp_path / "frames"

    frames = ChunkFrames(chunks, clock=clock, out_dir=out, extract=extract)()

    assert len(frames["cam"]) == 3
    assert sorted(p.name for p in out.iterdir()) == sorted(f.name for f in frames["cam"])


def test_a_minute_of_chunks_lets_a_fully_visible_concept_be_observable(tmp_path: Path) -> None:
    """The live finding: one frame per chunk left probes at 3-20 frames (lb 0.44)."""
    clock = FakeClock(EPOCH + timedelta(seconds=100))
    chunks = Chunks([_chunk("cam", f"/v/{i}.mp4", age_s=3 * i) for i in range(20)])
    frames = ChunkFrames(chunks, clock=clock, out_dir=tmp_path / "frames", extract=FakeExtract())()
    assert len(frames["cam"]) == 60

    segmenter = FrameSegmenter(lambda *_: 1)
    probe = probe_concept_on_camera("forklift", "cam", frames["cam"], segmenter=segmenter, seed=7)

    assert (probe.n_frames, probe.detect_rate) == (DEFAULT_PROBE_FRAMES, 1.0)
    assert probe.detect_lb == pytest.approx(0.862, abs=1e-3)
    assert probe.detect_lb >= OBSERVABLE_LB
    per_chunk = Counter(frame_group(Path(name)) for name, _, _ in segmenter.calls)
    assert len(per_chunk) == 20  # spread over every chunk ...
    assert max(per_chunk.values()) == math.ceil(DEFAULT_PROBE_FRAMES / 20)  # ... at most 2 each


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_ffmpeg_frames_writes_the_chosen_frames_in_one_run(tmp_path: Path) -> None:
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
            "testsrc=size=64x48:rate=10:duration=3",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(clip),
        ],
        check=True,
    )
    out = tmp_path / "out"
    out.mkdir()
    ffmpeg_frames(clip, (5, 15, 25), out)
    assert sorted(p.name for p in out.iterdir()) == ["1.jpg", "2.jpg", "3.jpg"]
    assert all(p.read_bytes().startswith(b"\xff\xd8\xff") for p in out.iterdir())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_chunk_frames_with_ffmpeg_end_to_end(tmp_path: Path) -> None:
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
            "testsrc=size=64x48:rate=10:duration=3",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(clip),
        ],
        check=True,
    )
    clock = FakeClock(EPOCH + timedelta(seconds=100))
    chunks = Chunks([_chunk("cam", str(clip), age_s=5)])
    frames = ChunkFrames(chunks, clock=clock, out_dir=tmp_path / "frames")()
    assert len(frames["cam"]) == 3
    assert len({f.read_bytes() for f in frames["cam"]}) == 3  # three different moments


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


def test_the_survey_window_is_a_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(tmp_path, "replay").planner_survey_window_s == 60.0
    monkeypatch.setenv("PLANNER_SURVEY_WINDOW_S", "45")
    settings = _settings(tmp_path, "replay")
    assert settings.planner_survey_window_s == 45.0
    clock = FakeClock()
    bus = Bus()
    cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
    planner = build_planner(
        settings,
        cache=cache,
        clock=clock,
        bus=bus,
        perception=build_perception(settings, cache=cache, clock=clock, bus=bus),
        plans=PlanRegistry(),
        site="demo",
    )
    assert planner is not None
    frames = planner._frames
    assert isinstance(frames, ChunkFrames)
    assert frames._window == timedelta(seconds=45)


@pytest.mark.parametrize(
    ("banner", "expected"),
    [
        (
            "ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright (c) 2000-2021",
            ["-vsync", "passthrough"],
        ),
        ("ffmpeg version 5.0.1 Copyright", ["-vsync", "passthrough"]),
        ("ffmpeg version 5.1.2 Copyright", ["-fps_mode", "passthrough"]),
        ("ffmpeg version n7.1 Copyright", ["-fps_mode", "passthrough"]),
        ("ffmpeg version 9.0 Copyright", ["-fps_mode", "passthrough"]),
        ("ffmpeg version N-117000-g1234abcd Copyright", ["-fps_mode", "passthrough"]),
        ("", ["-fps_mode", "passthrough"]),
    ],
)
def test_passthrough_flag_matches_the_ffmpeg_version(banner: str, expected: list[str]) -> None:
    assert runner_module.passthrough_flag(banner) == expected
