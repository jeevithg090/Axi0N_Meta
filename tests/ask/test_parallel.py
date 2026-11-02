"""Ask runs a turn's actions at the same time, in order, capped, and splits multi-statement SQL."""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest

from rulecam.ask import AskAgent, AskLimits, FrameGrabber, SqlError, TrackTables, open_tables
from rulecam.contracts import Detection
from rulecam.rules.store import ParquetTrackStore
from tests.ask.test_ask import Chunks, Sam, person, turn, write_frame
from tests.fakes import EPOCH, FakeClock, FakeLLM


@pytest.fixture
def root(tmp_path: Path) -> Path:
    tracks = tmp_path / "tracks"
    with ParquetTrackStore(tracks) as store:
        store.append([person("checkout:person:1", 0, 10), person("checkout:person:2", 4, 20)])
    return tracks


class MeetingSam(Sam):
    """Each call waits until `parties` calls are in flight at once (fails if they run in turn)."""

    def __init__(self, parties: int) -> None:
        super().__init__()
        self._barrier = threading.Barrier(parties, timeout=5)

    def segment_image(
        self, frame: Path, concept: str, *, camera_id: str = "x", include_confidence: bool = True
    ) -> list[Detection]:
        self._barrier.wait()
        return super().segment_image(frame, concept, camera_id=camera_id)


def count(concept: str) -> dict[str, object]:
    at = (EPOCH + timedelta(seconds=6)).isoformat()
    return {
        "thought": concept,
        "action": "count_objects",
        "concept": concept,
        "camera_id": "checkout",
        "times": [at],
    }


def build(spark: FakeLLM, root: Path, tmp_path: Path, sam: Sam, **limits: int) -> AskAgent:
    return AskAgent(
        spark=spark,
        segmenter=sam,
        glimmer=FakeLLM(),
        glimmer_model="muse-glimmer-30b",
        tables=open_tables(root),
        frames=FrameGrabber(Chunks(), tmp_path / "frames", extract=write_frame),  # type: ignore[arg-type]
        clock=FakeClock(EPOCH + timedelta(minutes=5)),
        limits=AskLimits(**limits),
    )


def test_a_turns_actions_run_at_once_and_come_back_in_order(root: Path, tmp_path: Path) -> None:
    spark = FakeLLM(
        [
            turn(count("person"), count("shopping cart"), count("basket")),
            turn({"thought": "done", "action": "answer", "answer": "3 things"}),
        ]
    )
    reply = build(spark, root, tmp_path, MeetingSam(3)).ask("What is there?")

    assert [s.action.concept for s in reply.steps[:3]] == ["person", "shopping cart", "basket"]
    assert {s.turn for s in reply.steps[:3]} == {0}
    message = str(spark.calls[1]["messages"][-1]["content"])  # type: ignore[index]
    assert message.startswith("Results, in the order of your actions:")
    assert "[1] count_objects:" in message
    assert "[3] count_objects:" in message


def test_actions_past_the_cap_are_reported_not_run(root: Path, tmp_path: Path) -> None:
    sam = Sam()
    spark = FakeLLM(
        [
            turn(count("a"), count("b"), count("c")),
            turn({"thought": "done", "action": "answer", "answer": "ok"}),
        ]
    )
    build(spark, root, tmp_path, sam, max_actions=2).ask("q")

    assert sorted(sam.prompts) == ["a", "b"]
    message = str(spark.calls[1]["messages"][-1]["content"])  # type: ignore[index]
    assert "Not run: your last 1 action(s) (at most 2 per turn)" in message


def test_an_answer_in_a_turn_ends_it(root: Path, tmp_path: Path) -> None:
    sam = Sam()
    spark = FakeLLM([turn(count("a"), {"thought": "t", "action": "answer", "answer": "Now."})])
    reply = build(spark, root, tmp_path, sam).ask("q")
    assert reply.answer == "Now."
    assert sam.prompts == []


def test_several_statements_run_one_by_one(root: Path) -> None:
    tables = TrackTables(root)
    results = tables.run_sql(
        "SELECT count(*) AS n FROM track_spans; SELECT ';' AS semicolon; SELECT nope FROM x"
    )
    assert [sql for sql, _ in results][:2] == [
        "SELECT count(*) AS n FROM track_spans",
        "SELECT ';' AS semicolon",
    ]
    first, second, third = (result for _, result in results)
    assert not isinstance(first, SqlError)
    assert first.rows == [[2]]
    assert not isinstance(second, SqlError)
    assert second.rows == [[";"]]
    assert isinstance(third, SqlError)
    with pytest.raises(SqlError, match="DROP"):
        tables.run_sql("SELECT 1; DROP TABLE track_points")
    assert tables.query("SELECT count(*) FROM track_points").rows != [[0]]
    tables.close()


def test_multi_statement_sql_reaches_spark_with_each_result(root: Path, tmp_path: Path) -> None:
    spark = FakeLLM(
        [
            turn({"thought": "two", "action": "sql", "sql": "SELECT 1 AS a; SELECT 2 AS b"}),
            turn({"thought": "done", "action": "answer", "answer": "ok"}),
        ]
    )
    reply = build(spark, root, tmp_path, Sam()).ask("q")
    results = reply.steps[0].result["results"]  # type: ignore[index]
    assert [(r["sql"], r["rows"]) for r in results] == [
        ("SELECT 1 AS a", [[1]]),
        ("SELECT 2 AS b", [[2]]),
    ]
