"""Spark Open reviews each Ask draft against its trace and can send it back once (add-on 2.7)."""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from rulecam.ask import AskAgent, FrameGrabber, open_tables
from rulecam.ask.agent import APOLOGY, REVIEW_STEPS
from rulecam.ask.review import SparkOpenReviewer, review_messages, sent_back
from rulecam.ask.trace import AskAction, AskAnswer, AskEvent, AskReview, AskStep
from rulecam.clients.open_weights.reasoning import ReasoningStore
from rulecam.clients.open_weights.spark_open import SparkOpenClient
from rulecam.clients.transport import ModelTransportError
from rulecam.rules.store import ParquetTrackStore
from tests.ask.test_ask import BrokenSpark, Chunks, Sam, person, turn, write_frame
from tests.fakes import EPOCH, FakeClock, FakeLLM
from tests.verify.test_tier2 import QueueTransport, spark_reply


@pytest.fixture
def root(tmp_path: Path) -> Path:
    tracks = tmp_path / "tracks"
    with ParquetTrackStore(tracks) as store:
        store.append([person("checkout:person:1", 0, 10), person("checkout:person:2", 4, 20)])
    return tracks


class Reviewer:
    """Returns preset verdicts and records what it was shown."""

    model = "spark-open-test"

    def __init__(self, *verdicts: str) -> None:
        self.verdicts = list(verdicts)
        self.seen: list[tuple[str, AskAction, int, int]] = []

    def review(
        self,
        question: str,
        draft: AskAction,
        steps: Sequence[AskStep],
        earlier: Sequence[AskAnswer] = (),
    ) -> AskReview:
        self.seen.append((question, draft, len(steps), len(earlier)))
        verdict = self.verdicts.pop(0)
        return AskReview(
            verdict=verdict,
            problems=["the count ignores the cut rows"] if verdict == "revise" else [],
            advice="count with SQL",
            draft=draft.answer or "",
            model=self.model,
        )


def build(spark: FakeLLM, root: Path, tmp_path: Path, reviewer: object) -> AskAgent:
    return AskAgent(
        spark=spark,
        segmenter=Sam(),
        glimmer=FakeLLM(),
        glimmer_model="muse-glimmer-30b",
        tables=open_tables(root),
        frames=FrameGrabber(Chunks(), tmp_path / "frames", extract=write_frame),  # type: ignore[arg-type]
        clock=FakeClock(EPOCH + timedelta(minutes=5)),
        reviewer=reviewer,  # type: ignore[arg-type]
    )


SQL: dict[str, object] = {
    "thought": "n",
    "action": "sql",
    "sql": "SELECT count(*) FROM track_spans",
}


def answer(text: str, numbers: list[float] | None = None) -> dict[str, object]:
    return turn({"thought": "done", "action": "answer", "answer": text, "numbers": numbers or []})


def test_an_accepted_draft_is_the_answer(root: Path, tmp_path: Path) -> None:
    reviewer = Reviewer("accept")
    events: list[AskEvent] = []
    reply = build(FakeLLM([turn(SQL), answer("Two.", [2])]), root, tmp_path, reviewer).ask(
        "How many?", emit=events.append
    )
    assert reply.answer == "Two."
    assert [r.verdict for r in reply.reviews] == ["accept"]
    assert reply.reviews[0].turn == 1
    assert reviewer.seen[0][1].answer == "Two."
    assert reviewer.seen[0][2] == 2, "the reviewer sees every step"
    kinds = [e.type for e in events]
    assert kinds[-3:] == ["reviewing", "review", "answer"]
    assert reply.models[-1] == "spark-open-test"


def test_a_revised_draft_goes_back_to_spark_once(root: Path, tmp_path: Path) -> None:
    spark = FakeLLM([answer("Ten.", [10]), turn(SQL), answer("Two.", [2])])
    reviewer = Reviewer("revise")
    reply = build(spark, root, tmp_path, reviewer).ask("How many?")

    assert reply.answer == "Two."
    assert reply.numbers == [2.0]
    (review,) = reply.reviews
    assert (review.verdict, review.draft, review.turn) == ("revise", "Ten.", 0)
    sent = spark.calls[1]["messages"]
    assert isinstance(sent, list)
    (k,) = [k for k, m in enumerate(sent) if "sent it back" in str(m["content"])]
    assert json.loads(sent[k - 1]["content"])["actions"][0]["answer"] == "Ten."
    assert "the count ignores the cut rows" in sent[k]["content"]
    assert [s.turn for s in reply.steps] == [0, 1, 2]
    assert len(reviewer.seen) == 1, "only the first draft is reviewed"


def test_after_a_send_back_spark_has_a_few_turns(root: Path, tmp_path: Path) -> None:
    spark = FakeLLM([answer("Ten."), *[turn(SQL)] * REVIEW_STEPS, answer("Two.")])
    reply = build(spark, root, tmp_path, Reviewer("revise")).ask("q")
    assert reply.answer == "Two."
    assert spark.calls[-1]["messages"][-1]["content"] == "Steps used up: answer now."  # type: ignore[index]


def test_stops_and_failures_are_not_reviewed(root: Path, tmp_path: Path) -> None:
    reviewer = Reviewer()
    broken = build(BrokenSpark(), root, tmp_path, reviewer).ask("q")
    assert broken.answer == APOLOGY
    stop = threading.Event()
    stop.set()
    stopped = build(FakeLLM(), root, tmp_path, reviewer).ask("q", stop=stop)
    assert stopped.reviews == []
    assert reviewer.seen == []


def test_a_request_gone_before_the_review_skips_it(root: Path, tmp_path: Path) -> None:
    stop = threading.Event()

    class Spark(FakeLLM):
        def structured(self, **kwargs: object) -> object:  # type: ignore[override]
            reply: object = super().structured(**kwargs)  # type: ignore[arg-type]
            stop.set()
            return reply

    reviewer = Reviewer()
    reply = build(Spark([answer("Two.")]), root, tmp_path, reviewer).ask("q", stop=stop)
    assert reply.answer == "Two."
    assert reviewer.seen == []


def spark_open(tmp_path: Path, *replies: object) -> tuple[SparkOpenClient, QueueTransport]:
    transport = QueueTransport(*replies)  # type: ignore[arg-type]
    client = SparkOpenClient(
        transport, clock=FakeClock(), reasoning=ReasoningStore(tmp_path / "reasoning")
    )
    return client, transport


DRAFT = AskAction(thought="counted", action="answer", answer="Five people.", numbers=[5])
STEP = AskStep(
    turn=0,
    action=AskAction(thought="n", action="sql", sql="SELECT 5"),
    result={"rows": [[5]], "note": "showing 1 of 1"},
    seconds=0.1,
)
EARLIER = AskAnswer(question="Before?", answer="Seven.", steps=[], seconds=1, models=[])


def test_spark_open_reviews_the_trace_and_keeps_its_reasoning(tmp_path: Path) -> None:
    verdict = {"verdict": "revise", "problems": ["contradicts Seven."], "advice": "explain"}
    client, transport = spark_open(tmp_path, spark_reply(verdict, reasoning="Seven vs five."))
    review = SparkOpenReviewer(client, max_result_chars=5000).review(
        "How many?", DRAFT, [STEP], [EARLIER]
    )
    assert (review.verdict, review.problems, review.advice) == (
        "revise",
        ["contradicts Seven."],
        "explain",
    )
    assert review.draft == "Five people."
    assert review.model == client.model
    assert review.reasoning_ref is not None
    assert (tmp_path / "reasoning" / f"{review.reasoning_ref}.txt").read_text() == "Seven vs five."
    body = transport.requests[0]["json"]
    case = json.loads(body["messages"][1]["content"])  # type: ignore[index]
    assert case["draft_numbers"] == [5]
    assert case["steps"][0]["result"]["rows"] == [[5]]
    assert case["earlier_in_this_conversation"][0]["answer"] == "Seven."
    assert body["reasoning_effort"] == "medium"  # type: ignore[index]


def test_a_failed_review_lets_the_draft_stand(root: Path, tmp_path: Path) -> None:
    client, _ = spark_open(tmp_path, ModelTransportError("timeout"), ModelTransportError("again"))
    reviewer = SparkOpenReviewer(client, max_result_chars=5000)
    reply = build(FakeLLM([answer("Two.")]), root, tmp_path, reviewer).ask("q")
    assert reply.answer == "Two."
    (review,) = reply.reviews
    assert review.verdict == "error"
    assert "ModelTransportError" in (review.detail or "")


def test_review_messages_skip_the_answer_step_and_fit_results() -> None:
    big = STEP.model_copy(update={"result": {"rows": [[k] for k in range(2000)]}})
    final = AskStep(turn=1, action=DRAFT, result=None, seconds=0.0)
    messages = review_messages("q", DRAFT, [big, final], [], 400)
    case = json.loads(str(messages[1]["content"]))
    assert len(case["steps"]) == 1
    assert len(json.dumps(case["steps"][0]["result"])) < 800


def test_sent_back_names_the_reviewer_and_its_findings() -> None:
    text = sent_back(AskReview(verdict="revise", model="spark-open", advice="recount"))
    assert "spark-open" in text
    assert "(none listed)" in text
    assert "recount" in text
