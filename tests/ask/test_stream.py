"""Ask streams each step as it happens, remembers conversations and serves the frames it used."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.api.ask import AskRequest, ask_router, sse, stream_answer
from rulecam.ask import AskAgent, FrameGrabber, open_tables
from rulecam.ask.trace import AskEvent
from rulecam.rules.store import ParquetTrackStore
from tests.ask.test_ask import Chunks, Sam, person, turn, write_frame
from tests.fakes import EPOCH, FakeClock, FakeLLM


@pytest.fixture
def root(tmp_path: Path) -> Path:
    tracks = tmp_path / "tracks"
    with ParquetTrackStore(tracks) as store:
        store.append([person("checkout:person:1", 0, 10), person("checkout:person:2", 4, 20)])
    return tracks


def counting(concept: str = "person") -> dict[str, object]:
    return {
        "thought": "count",
        "action": "count_objects",
        "concept": concept,
        "camera_id": "checkout",
        "times": [(EPOCH + timedelta(seconds=6)).isoformat()],
    }


def answer(text: str) -> dict[str, object]:
    return turn({"thought": "done", "action": "answer", "answer": text})


def build(spark: FakeLLM, root: Path, tmp_path: Path) -> AskAgent:
    return AskAgent(
        spark=spark,
        segmenter=Sam(),
        glimmer=FakeLLM(),
        glimmer_model="muse-glimmer-30b",
        tables=open_tables(root),
        frames=FrameGrabber(Chunks(), tmp_path / "frames", extract=write_frame),  # type: ignore[arg-type]
        clock=FakeClock(EPOCH + timedelta(minutes=5)),
        marks_dir=tmp_path / "marked",
    )


def test_each_step_is_emitted_as_it_happens(root: Path, tmp_path: Path) -> None:
    spark = FakeLLM([turn(counting(), counting("cart")), answer("Two.")])
    events: list[AskEvent] = []
    reply = build(spark, root, tmp_path).ask("How many?", emit=events.append)

    kinds = [e.type for e in events]
    assert kinds[:3] == ["start", "thinking", "actions"]
    assert sorted(kinds[3:5]) == ["step", "step"]
    assert kinds[5:] == ["thinking", "actions", "answer"]
    assert {e.index for e in events if e.type == "step"} == {0, 1}
    assert events[-1].answer == reply
    assert [t.turn for t in reply.turns] == [0, 1]
    assert reply.turns[0].actions == 2
    assert reply.steps[0].frames
    assert reply.conversation_id == events[0].conversation_id


def test_a_follow_up_sees_the_earlier_question_and_steps(root: Path, tmp_path: Path) -> None:
    spark = FakeLLM([turn(counting()), answer("Two people."), answer("Still two."), answer("New.")])
    agent = build(spark, root, tmp_path)
    first = agent.ask("How many people at noon?")
    second = agent.ask("And carts?", conversation_id=first.conversation_id)

    assert second.conversation_id == first.conversation_id
    opening = str(spark.calls[2]["messages"][1]["content"])  # type: ignore[index]
    history = json.loads(
        opening.split("Earlier in this conversation", 1)[1]
        .split(":\n", 1)[1]
        .split("\n\nOwner", maxsplit=1)[0]
    )
    assert history[0]["question"] == "How many people at noon?"
    assert history[0]["answer"] == "Two people."
    assert history[0]["steps"][0]["action"]["action"] == "count_objects"
    assert history[0]["steps"][0]["result"]["frames"][0]["count"] == 2
    fresh = agent.ask("Unrelated?")
    assert fresh.conversation_id != first.conversation_id
    assert "Earlier in this conversation" not in str(spark.calls[-1]["messages"][1]["content"])  # type: ignore[index]


def test_a_stopped_request_starts_no_turn(root: Path, tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    spark = FakeLLM()
    reply = build(spark, root, tmp_path).ask("q", stop=stop)
    assert reply.answer.startswith("Stopped")
    assert spark.calls == []


def test_only_frames_a_step_wrote_are_served(root: Path, tmp_path: Path) -> None:
    agent = build(FakeLLM([turn(counting()), answer("ok")]), root, tmp_path)
    (name,) = agent.ask("q").steps[0].frames
    assert agent.frame_file(name) == tmp_path / "frames" / name
    assert agent.frame_file("../tracks.jpg") is None
    assert agent.frame_file("missing.jpg") is None
    assert agent.frame_file("x.png") is None


def client(agent: AskAgent | None) -> TestClient:
    app = FastAPI()
    app.include_router(ask_router(agent))
    return TestClient(app)


def events_of(text: str) -> list[dict[str, object]]:
    blocks = [b for b in text.split("\n\n") if b.startswith("event:")]
    return [json.loads(b.split("data: ", 1)[1]) for b in blocks]


def test_the_stream_route_sends_events_then_the_answer(root: Path, tmp_path: Path) -> None:
    agent = build(FakeLLM([turn(counting()), answer("Two.")]), root, tmp_path)
    with client(agent).stream("POST", "/ask/stream", json={"question": "How many?"}) as reply:
        assert reply.status_code == 200
        assert reply.headers["content-type"].startswith("text/event-stream")
        text = "".join(reply.iter_text())
    events = events_of(text)
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "answer"
    assert events[-1]["answer"]["answer"] == "Two."  # type: ignore[index]
    (frame,) = events[-1]["answer"]["steps"][0]["frames"]  # type: ignore[index]
    image = client(agent).get(f"/ask/frames/{frame}")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert client(agent).get("/ask/frames/nope.jpg").status_code == 404


def test_the_json_route_continues_a_conversation(root: Path, tmp_path: Path) -> None:
    spark = FakeLLM([answer("One."), answer("Two.")])
    api = client(build(spark, root, tmp_path))
    first = api.post("/ask", json={"question": "a"}).json()
    second = api.post(
        "/ask", json={"question": "b", "conversation_id": first["conversation_id"]}
    ).json()
    assert second["conversation_id"] == first["conversation_id"]
    assert api.post("/ask", json={"question": "c", "conversation_id": "../x"}).status_code == 422


def test_without_an_agent_every_route_is_unavailable() -> None:
    api = client(None)
    assert api.post("/ask", json={"question": "a"}).status_code == 503
    assert api.post("/ask/stream", json={"question": "a"}).status_code == 503
    assert api.get("/ask/frames/a.jpg").status_code == 503


class Exploding:
    def ask(self, question: str, **kwargs: object) -> object:
        raise RuntimeError("boom")


class Slow:
    def ask(self, question: str, *, emit: object, stop: threading.Event, **kwargs: object) -> None:
        stop.wait(0.3)


async def test_a_crash_becomes_an_error_event_and_silence_a_heartbeat() -> None:
    body = AskRequest(question="q")
    crashed = [chunk async for chunk in stream_answer(Exploding(), body)]  # type: ignore[arg-type]
    assert crashed[-1].startswith("event: error")
    assert "RuntimeError" in crashed[-1]
    quiet = [chunk async for chunk in stream_answer(Slow(), body, heartbeat_s=0.05)]  # type: ignore[arg-type]
    assert ": keep-alive\n\n" in quiet


def test_an_event_is_one_sse_block() -> None:
    text = sse(AskEvent(type="thinking", turn=2))
    assert text.startswith("event: thinking\ndata: {")
    assert text.endswith("\n\n")
