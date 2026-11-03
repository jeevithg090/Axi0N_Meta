"""Ask's harness: captions (temporal memory), data stats, numbered marks, owner context."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from rulecam.ask import (
    AskAgent,
    CaptionMemory,
    FrameGrabber,
    TrackTables,
    camera_names,
    open_tables,
    planner_profiles,
)
from rulecam.ask.vision import Frame, marked
from rulecam.rules.store import ParquetTrackStore
from tests.ask.test_ask import Chunks, Sam, person, turn
from tests.fakes import EPOCH, FakeClock, FakeLLM


@pytest.fixture
def root(tmp_path: Path) -> Path:
    tracks = tmp_path / "tracks"
    with ParquetTrackStore(tracks) as store:
        store.append([person("checkout:person:1", 0, 10), person("checkout:person:2", 4, 20)])
    return tracks


def write_captions(path: Path) -> Path:
    rows = [
        {
            "camera_id": "checkout",
            "t0": (EPOCH + timedelta(seconds=k * 10)).isoformat(),
            "t1": (EPOCH + timedelta(seconds=k * 10 + 10)).isoformat(),
            "caption": f"window {k}: two customers queue at lane 2",
            "people": 2,
            "events": ["queue"],
        }
        for k in range(6)
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_captions_stats_and_boxes_are_in_the_snapshot(root: Path, tmp_path: Path) -> None:
    tables = TrackTables(root, write_captions(tmp_path / "captions.jsonl"))
    hits = tables.query("SELECT count(*) FROM captions WHERE caption ILIKE '%queue%'")
    assert hits.rows == [[6]]
    assert len(tables.recent_captions()) == 4
    (stat,) = tables.stats()
    assert (stat["concept"], stat["tracks"], stat["most_at_once"]) == ("person", 2, 2)
    near = tables.boxes_near("checkout", EPOCH + timedelta(seconds=6.1))
    assert [t for t, _, _ in near] == ["checkout:person:1", "checkout:person:2"]
    tables.close()


def test_marks_are_drawn_on_a_copy(tmp_path: Path) -> None:
    raw = tmp_path / "f.jpg"
    cv2.imwrite(str(raw), np.zeros((720, 1280, 3), dtype=np.uint8))
    frame = Frame(camera_id="checkout", at=EPOCH, path=raw)
    out = marked(frame, [("1", "person", (100.0, 100.0, 200.0, 400.0))], tmp_path / "marked")
    assert out.path != raw
    drawn = cv2.imread(str(out.path))
    assert drawn is not None
    assert drawn.sum() > 0
    assert marked(frame, [], tmp_path / "marked").path == raw


def test_owner_context_reaches_spark_and_look_returns_marks(root: Path, tmp_path: Path) -> None:
    (plans := tmp_path / "plans").mkdir()
    (plans / "p1.plan.yaml").write_text(
        "inventory:\n- {concept: checkout counter, camera_id: checkout, count: 3}\n"
    )
    at = (EPOCH + timedelta(seconds=6)).isoformat()
    spark = FakeLLM(
        [
            turn(
                {
                    "thought": "see",
                    "action": "look",
                    "camera_id": "checkout",
                    "times": [at],
                    "question": "Who is queueing?",
                }
            ),
            turn({"thought": "done", "action": "answer", "answer": "Two."}),
        ]
    )
    glimmer = FakeLLM([{"answer": "1 and 2 queue", "count": 2, "confidence": 0.9}])

    def frame_writer(chunk: Path, indices: Sequence[int], out_dir: Path) -> None:
        cv2.imwrite(str(out_dir / "1.jpg"), np.zeros((720, 1280, 3), dtype=np.uint8))

    agent = AskAgent(
        spark=spark,
        segmenter=Sam(),
        glimmer=glimmer,
        glimmer_model="muse-glimmer-30b",
        tables=open_tables(root, write_captions(tmp_path / "c.jsonl")),
        frames=FrameGrabber(Chunks(), tmp_path / "frames", extract=frame_writer),  # type: ignore[arg-type]
        clock=FakeClock(EPOCH + timedelta(minutes=5)),
        site_profile="Supermarket, 6 lanes",
        camera_names={"checkout": "checkout lanes"},
        camera_profiles=planner_profiles(plans),
        caption_every_s=10,
        marks_dir=tmp_path / "m",
    )
    reply = agent.ask("Who is queueing?")

    messages: list[dict[str, object]] = spark.calls[0]["messages"]  # type: ignore[assignment]
    context = json.loads(str(messages[1]["content"]).split("\n", 1)[1].split("\n\nOwner")[0])
    assert context["site_profile"] == "Supermarket, 6 lanes"
    assert context["camera_names"] == {"checkout": "checkout lanes"}
    assert (
        context["what_the_planner_saw_per_camera"]["checkout"][0]["concept"] == "checkout counter"
    )
    assert len(context["recent_captions"]) == 4
    assert "every 10 s" in str(messages[0]["content"])
    assert reply.steps[0].result["marks"] == {"1": "checkout:person:1", "2": "checkout:person:2"}  # type: ignore[index]


def test_camera_names_are_parsed() -> None:
    assert camera_names(" phone-62=checkout lanes, bad ,phone-61 = entrance=queue") == {
        "phone-62": "checkout lanes",
        "phone-61": "entrance=queue",
    }


def test_the_caption_memory_writes_without_blocking(tmp_path: Path) -> None:
    def frame_writer(chunk: Path, indices: Sequence[int], out_dir: Path) -> None:
        (out_dir / "1.jpg").write_bytes(b"x")

    glimmer = FakeLLM([{"caption": "Two customers at lane 2.", "people": 2, "events": ["queue"]}])
    path = tmp_path / "captions.jsonl"
    memory = CaptionMemory(
        frames=FrameGrabber(Chunks(), tmp_path / "f", extract=frame_writer),  # type: ignore[arg-type]
        glimmer=glimmer,
        glimmer_model="muse-glimmer-30b",
        path=path,
        every_s=10,
        clock=FakeClock(EPOCH),
        site_profile="Supermarket",
        camera_names={"checkout": "lanes"},
    )
    started = time.perf_counter()
    memory.tick()
    assert time.perf_counter() - started < 0.5
    for _ in range(50):
        if path.is_file() and path.read_text():
            break
        time.sleep(0.05)
    row = json.loads(path.read_text().splitlines()[0])
    assert (row["camera_id"], row["people"]) == ("checkout", 2)
    prompt = str(glimmer.calls[0]["messages"][0]["content"][0]["text"])  # type: ignore[index]
    assert "Supermarket" in prompt
    assert "lanes" in prompt
