"""Ask's `watch` tool (add-on 2.8): recorded chunks joined into one clip that Spark watches."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rulecam.ask import AskAgent, FrameGrabber, open_tables
from rulecam.ask.watch import (
    WATCH_MODEL,
    ClipJoiner,
    ffmpeg_join,
    spaced,
)
from rulecam.clients.errors import ModelHTTPError
from rulecam.contracts import Chunk
from rulecam.rules.store import ParquetTrackStore
from tests.ask.test_ask import Sam, person, turn, write_frame
from tests.fakes import EPOCH, FakeClock, FakeLLM, make_chunk


@pytest.fixture
def root(tmp_path: Path) -> Path:
    tracks = tmp_path / "tracks"
    with ParquetTrackStore(tracks) as store:
        store.append([person("checkout:person:1", 0, 10)])
    return tracks


class Chunks:
    """3 s chunks of `checkout` from EPOCH, with one missing (a gap)."""

    def __init__(self, indices: Sequence[int] = (0, 1, 2, 3, 5, 6, 7, 8, 9)) -> None:
        self.recorded = [make_chunk("checkout", i) for i in indices]

    def last_seen(self) -> dict[str, datetime]:
        return {"checkout": self.recorded[-1].start_ts}

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        return [c for c in self.recorded if c.camera_id == camera_id and t0 <= c.start_ts <= t1]


class FakeJoin:
    def __init__(self, broken: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.broken = broken

    def __call__(self, chunks: Sequence[Path], target: Path) -> None:
        if self.broken:
            raise subprocess.CalledProcessError(1, ["ffmpeg"])
        self.calls.append([p.name for p in chunks])
        target.write_bytes(b"mp4:" + "|".join(p.name for p in chunks).encode())


def at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def test_a_window_joins_the_chunks_it_overlaps(tmp_path: Path) -> None:
    join = FakeJoin()
    joiner = ClipJoiner(Chunks(), tmp_path / "clips", join=join)
    clip = joiner.clip("checkout", at(4), at(10))
    assert join.calls == [["checkout_000001.mp4", "checkout_000002.mp4", "checkout_000003.mp4"]]
    assert (clip.start, clip.end, clip.chunks) == (at(3), at(12), 3)
    assert clip.seconds == 9
    assert clip.notes == []
    again = joiner.clip("checkout", at(4), at(10))
    assert again.path == clip.path
    assert len(join.calls) == 1, "a clip already joined is reused"
    assert not list((tmp_path / "clips").glob("*.part.mp4"))


def test_gaps_and_the_window_cap_are_named(tmp_path: Path) -> None:
    joiner = ClipJoiner(Chunks(), tmp_path / "clips", max_seconds=20, join=FakeJoin())
    clip = joiner.clip("checkout", at(9), at(60))
    assert clip.chunks == 6  # chunks 3, 5, 6, 7, 8, 9 (4 is missing)
    assert any("first 20 s" in n and at(29).isoformat() in n for n in clip.notes)
    assert any("not recorded" in n and at(12).isoformat() in n for n in clip.notes)


def test_bad_windows_and_failed_joins_raise(tmp_path: Path) -> None:
    joiner = ClipJoiner(Chunks(), tmp_path / "clips", join=FakeJoin())
    with pytest.raises(ValueError, match="after its start"):
        joiner.clip("checkout", at(5), at(5))
    with pytest.raises(ValueError, match="nothing recorded"):
        joiner.clip("elsewhere", at(0), at(5))
    broken = ClipJoiner(Chunks(), tmp_path / "broken", join=FakeJoin(broken=True))
    with pytest.raises(OSError, match="CalledProcessError"):
        broken.clip("checkout", at(0), at(5))
    assert not list((tmp_path / "broken").glob("*"))


def test_spaced_instants_cover_the_window() -> None:
    assert spaced(at(0), at(10), 5) == [at(1), at(3), at(5), at(7), at(9)]
    assert spaced(at(0), at(10), 1) == [at(0)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_ffmpeg_joins_real_chunks_without_reencoding(tmp_path: Path) -> None:
    parts = []
    for k, colour in enumerate(("red", "blue")):
        part = tmp_path / f"it's {k}.mp4"  # a quote in the path must survive the concat list
        subprocess.run(  # noqa: S603 - fixed argument list
            [  # noqa: S607
                "ffmpeg",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={colour}:s=64x48:r=10:d=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(part),
            ],
            check=True,
        )
        parts.append(part)
    target = tmp_path / "joined.mp4"
    ffmpeg_join(parts, target)
    probe = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(probe.stdout)["streams"][0]["nb_read_frames"] == "20"
    assert not list(tmp_path.glob("*.txt")), "the concat list is removed"


class Files:
    def __init__(self, broken: bool = False) -> None:
        self.attached: list[Path] = []
        self.broken = broken

    def attach(self, path: Path) -> dict[str, object]:
        if self.broken:
            raise ModelHTTPError("upload rejected", status=413, attempts=1)
        self.attached.append(path)
        return {"type": "input_video", "file_id": f"file-{path.stem}"}


def agent(
    spark: FakeLLM, glimmer: FakeLLM, root: Path, tmp_path: Path, files: Files | None
) -> AskAgent:
    chunks = Chunks()
    return AskAgent(
        spark=spark,
        segmenter=Sam(),
        glimmer=glimmer,
        glimmer_model="muse-glimmer-30b",
        tables=open_tables(root),
        frames=FrameGrabber(chunks, tmp_path / "frames", extract=write_frame),
        clock=FakeClock(EPOCH + timedelta(minutes=5)),
        clips=ClipJoiner(chunks, tmp_path / "clips", join=FakeJoin()),
        files=files,
    )


WATCH: dict[str, object] = {
    "thought": "entries only show as movement",
    "action": "watch",
    "camera_id": "checkout",
    "times": [at(3).isoformat(), at(9).isoformat()],
    "question": "How many people walk into the shop?",
}
DONE = turn({"thought": "done", "action": "answer", "answer": "One."})


def test_spark_watches_the_clip_and_its_moments_become_instants(root: Path, tmp_path: Path) -> None:
    seen = {
        "answer": "One person walks into the shop on the left.",
        "count": 1,
        "moments": [{"at_s": 4.5, "what": "a woman in red enters the shop"}],
        "confidence": 0.7,
    }
    spark = FakeLLM([turn(WATCH), seen, DONE])
    files = Files()
    reply = agent(spark, FakeLLM(), root, tmp_path, files).ask("How many went into shops?")

    step = reply.steps[0]
    assert step.model == WATCH_MODEL
    result = step.result
    assert isinstance(result, dict)
    assert result["count"] == 1
    moment = {"at": at(7.5).isoformat(), "what": "a woman in red enters the shop"}
    assert result["moments"] == [moment]
    assert result["clip"]["start"] == at(3).isoformat()
    assert result["clip"]["chunks"] == 2
    watched = spark.calls[1]
    assert watched["model"] == WATCH_MODEL
    assert watched["schema"] == "WatchAnswer"
    content = watched["messages"][1]["content"]  # type: ignore[index]
    assert content[1] == {"type": "input_video", "file_id": f"file-{files.attached[0].stem}"}
    assert "How many people walk into the shop?" in content[0]["text"]
    assert WATCH_MODEL in reply.models
    system = str(spark.calls[0]["messages"][0]["content"])  # type: ignore[index]
    assert "4. watch" in system
    assert "180 s" in system


def test_a_failed_video_call_falls_back_to_glimmer_stills(root: Path, tmp_path: Path) -> None:
    glimmer = FakeLLM([{"answer": "Someone near the door.", "count": 1, "confidence": 0.4}])
    spark = FakeLLM([turn(WATCH), DONE])
    reply = agent(spark, glimmer, root, tmp_path, Files(broken=True)).ask("q")

    step = reply.steps[0]
    assert step.model == "muse-glimmer-30b"
    result = step.result
    assert isinstance(result, dict)
    assert "ModelHTTPError" in str(result["fallback"])
    assert result["answer"] == "Someone near the door."
    assert len(step.frames) == 6
    assert glimmer.calls[0]["schema"] == "LookAnswer"


def test_watch_needs_a_window_and_an_uploader(root: Path, tmp_path: Path) -> None:
    one_time: dict[str, object] = {**WATCH, "times": [at(3).isoformat()]}
    spark = FakeLLM([turn(one_time), DONE])
    reply = agent(spark, FakeLLM(), root, tmp_path, Files()).ask("q")
    assert "start, end" in str(reply.steps[0].result)
    spark = FakeLLM([turn(WATCH), DONE])
    reply = agent(spark, FakeLLM(), root, tmp_path, None).ask("q")
    assert "not available" in str(reply.steps[0].result)
