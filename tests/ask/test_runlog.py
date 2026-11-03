"""Ask's run log: one hash-chained row per answer, its hash returned as the answer's receipt."""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from rulecam.ask import AskAgent, FrameGrabber, open_tables
from rulecam.ask.agent import PROMPT_SHA256, SYSTEM_PROMPT, prompt_digest
from rulecam.ask.review import REVIEW_PROMPT
from rulecam.ask.runlog import GENESIS, AskRunLog, git_sha, row_hash, verify_run_log
from rulecam.rules.store import ParquetTrackStore
from tests.ask.test_ask import Chunks, Sam, person, turn, write_frame
from tests.fakes import EPOCH, FakeClock, FakeLLM


@pytest.fixture
def root(tmp_path: Path) -> Path:
    tracks = tmp_path / "tracks"
    with ParquetTrackStore(tracks) as store:
        store.append([person("checkout:person:1", 0, 10), person("checkout:person:2", 4, 20)])
    return tracks


def run_log(path: Path, max_chars: int = 50_000) -> AskRunLog:
    return AskRunLog(path, clock=FakeClock(EPOCH), git_sha="abc123", max_result_chars=max_chars)


def build(spark: FakeLLM, root: Path, tmp_path: Path, runs: AskRunLog | None) -> AskAgent:
    return AskAgent(
        spark=spark,
        segmenter=Sam(),
        glimmer=FakeLLM(),
        glimmer_model="muse-glimmer-30b",
        tables=open_tables(root),
        frames=FrameGrabber(Chunks(), tmp_path / "frames", extract=write_frame),  # type: ignore[arg-type]
        clock=FakeClock(EPOCH + timedelta(minutes=5)),
        runs=runs,
    )


def sql_then_answer(numbers: list[float | None]) -> list[dict[str, object]]:
    return [
        turn({"thought": "n", "action": "sql", "sql": "SELECT count(*) FROM track_spans"}),
        turn({"thought": "done", "action": "answer", "answer": "Two.", "numbers": numbers}),
    ]


def test_each_answer_is_a_chained_row_and_its_hash_the_receipt(root: Path, tmp_path: Path) -> None:
    log = run_log(tmp_path / "ask" / "runs.jsonl")
    spark = FakeLLM([*sql_then_answer([2]), *sql_then_answer([None, 3.5])])
    agent = build(spark, root, tmp_path, log)

    first = agent.ask("How many?", numbers=["people"])
    second = agent.ask("And then?")

    rows = [json.loads(line) for line in log.path.read_text("utf-8").splitlines()]
    assert [r["sha256"] for r in rows] == [first.receipt, second.receipt]
    assert rows[0]["prev_sha256"] == GENESIS
    assert rows[1]["prev_sha256"] == first.receipt
    assert first.numbers == [2.0]
    assert second.numbers == [None, 3.5]
    assert rows[0]["numbers"] == [2.0]
    assert rows[0]["git_sha"] == "abc123"
    assert rows[0]["prompt_sha256"] == PROMPT_SHA256 == first.prompt_sha256
    assert rows[0]["steps"][0]["action"]["sql"].startswith("SELECT count")
    assert rows[0]["steps"][0]["result"]["rows"] == [[2]]
    assert rows[0]["turns"][0]["turn"] == 0
    assert verify_run_log(log.path) == 2


def test_the_wanted_numbers_are_named_to_spark(root: Path, tmp_path: Path) -> None:
    spark = FakeLLM(sql_then_answer([2]))
    build(spark, root, tmp_path, None).ask("How many?", numbers=["people at noon", "carts"])
    opening = str(spark.calls[0]["messages"][1]["content"])  # type: ignore[index]
    assert '["people at noon", "carts"]' in opening
    assert opening.index("Owner's question") < opening.index("people at noon")


def test_a_reopened_log_continues_the_chain(tmp_path: Path, root: Path) -> None:
    path = tmp_path / "runs.jsonl"
    build(FakeLLM(sql_then_answer([1])), root, tmp_path, run_log(path)).ask("a")
    again = build(FakeLLM(sql_then_answer([1])), root, tmp_path, run_log(path)).ask("b")
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert rows[1]["prev_sha256"] == rows[0]["sha256"]
    assert again.receipt == rows[1]["sha256"]
    assert verify_run_log(path) == 2


def test_an_edited_row_breaks_the_chain(tmp_path: Path, root: Path) -> None:
    path = tmp_path / "runs.jsonl"
    agent = build(FakeLLM(sql_then_answer([1]) * 2), root, tmp_path, run_log(path))
    agent.ask("a")
    agent.ask("b")
    rows = path.read_text("utf-8").splitlines()
    edited = json.loads(rows[0])
    edited["answer"] = "Three."
    path.write_text("\n".join([json.dumps(edited), rows[1]]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="row 1"):
        verify_run_log(path)


def test_a_failed_write_still_answers(root: Path, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a folder", encoding="utf-8")
    log = run_log(blocker / "runs.jsonl")
    reply = build(FakeLLM(sql_then_answer([2])), root, tmp_path, log).ask("q")
    assert reply.answer == "Two."
    assert reply.receipt == ""


def test_long_results_are_kept_within_the_budget(tmp_path: Path, root: Path) -> None:
    path = tmp_path / "runs.jsonl"
    spark = FakeLLM(
        [
            turn({"thought": "all", "action": "sql", "sql": "SELECT * FROM track_points"}),
            turn({"thought": "done", "action": "answer", "answer": "ok"}),
        ]
    )
    build(spark, root, tmp_path, run_log(path, max_chars=300)).ask("q")
    row = json.loads(path.read_text("utf-8"))
    assert len(json.dumps(row["steps"][0]["result"])) < 600
    assert verify_run_log(path) == 1


def test_row_hash_ignores_its_own_hash_field() -> None:
    row: dict[str, object] = {"a": 1, "prev_sha256": GENESIS}
    assert row_hash(GENESIS, row) == row_hash(GENESIS, {**row, "sha256": "x"})


def test_the_prompt_digest_changes_with_the_prompt() -> None:
    assert prompt_digest(SYSTEM_PROMPT, REVIEW_PROMPT) == PROMPT_SHA256
    assert prompt_digest(SYSTEM_PROMPT + " ", REVIEW_PROMPT) != PROMPT_SHA256


def test_git_sha_prefers_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    assert git_sha("deadbeef") == "deadbeef"

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    assert git_sha() == ""

    def found(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["git"], 0, stdout="cafe\n", stderr="")

    monkeypatch.setattr(subprocess, "run", found)
    assert git_sha() == "cafe"
