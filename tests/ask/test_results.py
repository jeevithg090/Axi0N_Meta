"""Ask never cuts a result silently: row caps, character budgets and frame caps are reported."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from rulecam.ask import AskAgent, AskLimits, FrameGrabber, TrackTables, open_tables
from rulecam.ask.results import fit_result, fit_results
from rulecam.rules.store import ParquetTrackStore
from tests.ask.test_ask import Chunks, Sam, person, turn, write_frame
from tests.fakes import EPOCH, FakeClock, FakeLLM


@pytest.fixture
def root(tmp_path: Path) -> Path:
    tracks = tmp_path / "tracks"
    with ParquetTrackStore(tracks) as store:
        store.append([person(f"checkout:person:{k}", k, 10) for k in range(5)])
    return tracks


def table(n: int) -> dict[str, object]:
    return {"columns": ["k", "text"], "rows": [[k, "x" * 50] for k in range(n)]}


def test_a_short_result_goes_through_whole() -> None:
    assert json.loads(fit_result(table(3), 10_000)) == table(3)


def test_a_long_table_keeps_leading_rows_and_says_how_many() -> None:
    text = fit_result({**table(500), "total_rows": 800, "truncated": True}, 4000)
    cut = json.loads(text)
    assert len(text) <= 4000
    assert cut["rows_shown"] == len(cut["rows"]) > 0
    assert cut["rows"][0] == [0, "x" * 50]
    assert f"showing {cut['rows_shown']} of 800 rows" in cut["note"]


def test_anything_else_is_wrapped_as_valid_partial_json() -> None:
    text = fit_result({"answer": "y" * 10_000, "quote": '"' * 3000}, 3000)
    cut = json.loads(text)
    assert len(text) <= 3000
    assert cut["total_chars"] > 3000
    assert "cut at" in cut["note"]
    assert cut["partial_json"].startswith('{"answer": "yyy')


def test_several_results_share_a_budget_small_ones_whole() -> None:
    small, big = {"ok": 1}, table(400)
    texts = fit_results([big, small, table(400)], 6000)
    assert json.loads(texts[1]) == small
    assert sum(len(t) for t in texts) <= 6000
    assert all(json.loads(t)["rows_shown"] > 0 for t in (texts[0], texts[2]))


def test_a_query_over_the_row_cap_reports_the_full_count(root: Path) -> None:
    tables = TrackTables(root, max_rows=7)
    capped = tables.query("SELECT track_id, ts FROM track_points ORDER BY ts")
    whole = tables.query("SELECT count(*) FROM track_points")
    assert len(capped.rows) == 7
    assert capped.truncated
    assert capped.total_rows == whole.rows[0][0]
    assert capped.note is not None
    assert f"of {capped.total_rows} rows" in capped.note
    assert tables.query("SELECT 1").total_rows is None
    tables.close()


def test_instants_past_the_frame_cap_or_unrecorded_are_named(root: Path, tmp_path: Path) -> None:
    times = [(EPOCH + timedelta(seconds=s)).isoformat() for s in (3, 6, 9, 9000)]
    spark = FakeLLM(
        [
            turn(
                {
                    "thought": "count",
                    "action": "count_objects",
                    "concept": "person",
                    "camera_id": "checkout",
                    "times": times,
                }
            ),
            turn({"thought": "big", "action": "sql", "sql": "SELECT * FROM track_points"}),
            turn({"thought": "done", "action": "answer", "answer": "ok"}),
        ]
    )
    agent = AskAgent(
        spark=spark,
        segmenter=Sam(),
        glimmer=FakeLLM(),
        glimmer_model="muse-glimmer-30b",
        tables=open_tables(root, max_rows=5),
        frames=FrameGrabber(Chunks(), tmp_path / "frames", extract=write_frame),  # type: ignore[arg-type]
        clock=FakeClock(EPOCH + timedelta(minutes=5)),
        limits=AskLimits(max_rows=5, max_result_chars=2500, max_frames=3),
    )
    reply = agent.ask("How many?")

    notes = reply.steps[0].result["notes"]  # type: ignore[index]
    assert "used the first 3 of 4 instants" in notes[0]
    assert len(reply.steps[0].result["frames"]) == 3  # type: ignore[index]
    messages: list[dict[str, object]] = spark.calls[2]["messages"]  # type: ignore[assignment]
    sql_result = json.loads(str(messages[-1]["content"]).removeprefix("Result:\n"))
    assert sql_result["truncated"]
    assert "showing the first 5 of" in sql_result["note"]
    assert "up to 5 rows come back" in str(messages[0]["content"])
    assert "at up to 3 instants" in str(messages[0]["content"])
