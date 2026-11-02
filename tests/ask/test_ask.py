"""Ask: Spark answers owner questions with SQL over the tracks, SAM counts and Glimmer looks."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from rulecam.ask import AskAgent, FrameGrabber, SqlError, TrackTables, open_tables
from rulecam.clients.errors import StructuredOutputError
from rulecam.contracts import Detection, Mask, Track, TrackPoint
from rulecam.rules.store import ParquetTrackStore
from tests.fakes import EPOCH, FakeClock, FakeLLM, make_chunk


def person(track_id: str, start_s: float, seconds: float, camera_id: str = "checkout") -> Track:
    points = tuple(
        TrackPoint(
            ts=EPOCH + timedelta(seconds=start_s + k * 0.5),
            frame_idx=k,
            box_xyxy=(100.0, 100.0, 200.0, 400.0),
            foot_px=(150.0, 400.0),
        )
        for k in range(int(seconds * 2) + 1)
    )
    return Track(track_id=track_id, camera_id=camera_id, concept="person", points=points)


@pytest.fixture
def tracks_root(tmp_path: Path) -> Path:
    root = tmp_path / "tracks"
    with ParquetTrackStore(root) as store:
        store.append([person("checkout:person:1", 0, 10), person("checkout:person:2", 4, 20)])
    return root


def test_the_snapshot_answers_sql_over_tracks_and_spans(tracks_root: Path) -> None:
    tables = TrackTables(tracks_root)
    spans = tables.query("SELECT track_id, seconds FROM track_spans ORDER BY track_id")
    assert spans.rows == [["checkout:person:1", 10.0], ["checkout:person:2", 20.0]]
    at = (EPOCH + timedelta(seconds=6)).strftime("%Y-%m-%d %H:%M:%S")
    present = tables.query(f"SELECT count(*) FROM track_points WHERE ts = TIMESTAMP '{at}'")  # noqa: S608
    assert present.rows == [[2]]
    (camera,) = tables.cameras()
    assert (camera.camera_id, camera.concepts) == ("checkout", {"person": 2})
    tables.close()


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE track_points",
        "SELECT 1; SELECT 2",
        "COPY track_points TO 'out.csv'",
        "SELECT * FROM read_csv('/etc/hosts')",
        "SELECT nope FROM track_points",
    ],
)
def test_only_reads_of_the_snapshot_run(tracks_root: Path, sql: str) -> None:
    tables = TrackTables(tracks_root)
    with pytest.raises(SqlError):
        tables.query(sql)
    tables.close()


def test_an_empty_store_still_has_tables(tmp_path: Path) -> None:
    tables = TrackTables(tmp_path / "none")
    assert tables.query("SELECT count(*) FROM track_spans").rows == [[0]]
    assert tables.cameras() == []


class Chunks:
    def __init__(self) -> None:
        self.recorded = [make_chunk("checkout", i) for i in range(10)]

    def last_seen(self) -> dict[str, object]:
        return {"checkout": self.recorded[-1].start_ts}

    def chunks(self, camera_id: str, t0: object, t1: object) -> list[object]:
        return [c for c in self.recorded if c.camera_id == camera_id]


def write_frame(chunk: Path, indices: Sequence[int], out_dir: Path) -> None:
    (out_dir / "1.jpg").write_bytes(f"{chunk}|{list(indices)}".encode())


class Sam:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def segment_image(
        self, frame: Path, concept: str, *, camera_id: str = "x", include_confidence: bool = True
    ) -> list[Detection]:
        self.prompts.append(concept)
        mask = Mask(rle="", box_xyxy=(0.0, 0.0, 1.0, 1.0), confidence=0.9)
        found = Detection(
            camera_id=camera_id, frame_idx=0, ts=EPOCH, concept=concept, object_id=1, mask=mask
        )
        return [found, found.model_copy(update={"object_id": 2})]


def agent(
    spark: FakeLLM, glimmer: FakeLLM, tracks_root: Path, tmp_path: Path, sam: Sam
) -> AskAgent:
    return AskAgent(
        spark=spark,
        segmenter=sam,
        glimmer=glimmer,
        glimmer_model="muse-glimmer-30b",
        tables=open_tables(tracks_root),
        frames=FrameGrabber(Chunks(), tmp_path / "frames", extract=write_frame),  # type: ignore[arg-type]
        clock=FakeClock(EPOCH + timedelta(minutes=5)),
        timezone="Pacific/Auckland",
    )


def test_spark_chooses_the_tools_and_answers(tracks_root: Path, tmp_path: Path) -> None:
    at = (EPOCH + timedelta(seconds=6)).isoformat()
    spark = FakeLLM(
        [
            {"thought": "dwell", "action": "sql", "sql": "SELECT avg(seconds) FROM track_spans"},
            {
                "thought": "carts",
                "action": "count_objects",
                "concept": "shopping cart",
                "camera_id": "checkout",
                "times": [at],
            },
            {
                "thought": "check",
                "action": "look",
                "camera_id": "checkout",
                "times": [at.replace("+00:00", "Z")],
                "question": "How many customers queue?",
            },
            {"thought": "done", "action": "answer", "answer": "15 s on average, 2 carts."},
        ]
    )
    glimmer = FakeLLM([{"answer": "Two customers queue.", "count": 2, "confidence": 0.8}])
    sam = Sam()

    reply = agent(spark, glimmer, tracks_root, tmp_path, sam).ask("How long do customers stay?")

    assert reply.answer == "15 s on average, 2 carts."
    assert [s.action.action for s in reply.steps] == ["sql", "count_objects", "look", "answer"]
    assert reply.steps[0].result["rows"] == [[15.0]]  # type: ignore[index]
    assert reply.steps[1].result["frames"][0]["count"] == 2  # type: ignore[index]
    assert reply.steps[2].result["count"] == 2  # type: ignore[index]
    assert sam.prompts == ["shopping cart"]
    assert reply.models == ["muse-spark-1.3", "sam-3.1", "muse-glimmer-30b"]
    messages: list[dict[str, object]] = spark.calls[0]["messages"]  # type: ignore[assignment]
    opening = json.loads(str(messages[1]["content"]).split("\n", 1)[1].split("\n\n")[0])
    assert opening["site_timezone"] == "Pacific/Auckland"
    assert opening["tracked_objects_per_camera"][0]["concepts"] == {"person": 2}
    assert "checkout" in opening["recorded_video_per_camera"]


def test_tool_errors_go_back_to_spark(tracks_root: Path, tmp_path: Path) -> None:
    spark = FakeLLM(
        [
            {"thought": "bad", "action": "sql", "sql": "DELETE FROM track_points"},
            {
                "thought": "gone",
                "action": "look",
                "camera_id": "elsewhere",
                "times": ["2026-10-30T12:00:01"],
            },
            {"thought": "ok", "action": "answer", "answer": "Not recorded."},
        ]
    )
    reply = agent(spark, FakeLLM(), tracks_root, tmp_path, Sam()).ask("q")
    assert "error" in reply.steps[0].result  # type: ignore[operator]
    assert "error" in reply.steps[1].result  # type: ignore[operator]
    assert reply.answer == "Not recorded."


def test_the_loop_is_bounded(tracks_root: Path, tmp_path: Path) -> None:
    looping: dict[str, object] = {"thought": "again", "action": "sql", "sql": "SELECT 1"}
    spark = FakeLLM([looping] * 8 + [{"thought": "x", "action": "answer", "answer": "best effort"}])
    reply = agent(spark, FakeLLM(), tracks_root, tmp_path, Sam()).ask("q")
    assert len(reply.steps) == 9
    assert reply.answer == "best effort"
    assert spark.calls[-1]["messages"][-1]["content"] == "Steps used up: answer now."  # type: ignore[index]


class BrokenSpark(FakeLLM):
    def structured(self, **kwargs: object) -> object:  # type: ignore[override]
        raise StructuredOutputError("bad JSON twice")


def test_a_broken_spark_reply_ends_with_an_apology(tracks_root: Path, tmp_path: Path) -> None:
    reply = agent(BrokenSpark(), FakeLLM(), tracks_root, tmp_path, Sam()).ask("q")
    assert reply.answer.startswith("Sorry")
    assert "StructuredOutputError" in reply.steps[-1].action.thought
