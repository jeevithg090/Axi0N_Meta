"""`rulecam eval ask`: ask a question set N times over SSE and grade the structured numbers."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.eval import ask as ask_module
from rulecam.eval.ask import (
    AskQuestion,
    ExpectedNumber,
    HttpAskApi,
    QuestionSet,
    load_question_set,
    render_markdown,
    run_benchmark,
    save_benchmark,
    score_number,
    sse_events,
)
from rulecam.eval.errors import EvalRunError, EvalSetError

REPO = Path(__file__).resolve().parents[2]
CORRIDOR = REPO / "eval" / "ask" / "corridor.yaml"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def test_the_corridor_set_loads() -> None:
    corridor = load_question_set(CORRIDOR)
    assert corridor.name == "corridor"
    assert [q.id for q in corridor.questions] == [f"C{k:02d}" for k in range(1, 11)]
    c07 = next(q for q in corridor.questions if q.id == "C07")
    assert [n.truth for n in c07.numbers] == [5, 1, 4]
    assert not next(q for q in corridor.questions if q.id == "C08").numbers


def test_a_bad_set_is_an_eval_set_error(tmp_path: Path) -> None:
    dup = tmp_path / "dup.yaml"
    dup.write_text(
        "name: x\nquestions:\n  - {id: A, question: q}\n  - {id: A, question: r}\n", "utf-8"
    )
    with pytest.raises(EvalSetError, match="unique"):
        load_question_set(dup)
    with pytest.raises(EvalSetError):
        load_question_set(tmp_path / "missing.yaml")


def test_tolerance_is_the_larger_of_absolute_and_relative() -> None:
    n = ExpectedNumber(name="n", truth=80, abs_tol=2, rel_tol=0.15)
    assert n.tolerance == pytest.approx(12)
    assert score_number(n, 92).passed
    assert not score_number(n, 92.5).passed
    assert not score_number(n, None).passed
    assert score_number(n, 70).error == -10


def test_sse_events_are_split_and_comments_skipped() -> None:
    lines = [
        ": keep-alive",
        "",
        "event: step",
        "data: {}",
        "",
        "event: answer",
        "data: {",
        "data: }",
    ]
    assert list(sse_events(iter(lines))) == [("step", "{}"), ("answer", "{\n}")]


class FakeApi:
    """Answers from a script: per question, a list of replies (an exception string fails)."""

    def __init__(self, script: dict[str, list[object]]) -> None:
        self.script = script
        self.asked: list[tuple[str, list[str]]] = []

    def ask(self, question: str, numbers: Sequence[str]) -> dict[str, object]:
        self.asked.append((question, list(numbers)))
        reply = self.script[question].pop(0)
        if isinstance(reply, str):
            raise EvalRunError(reply)
        assert isinstance(reply, dict)
        return reply


def answer(numbers: list[object], seconds: float = 10.0) -> dict[str, object]:
    return {
        "answer": "text",
        "numbers": numbers,
        "seconds": seconds,
        "steps": [{}, {}],
        "models": ["muse-spark-1.3"],
        "receipt": "f" * 64,
    }


QUESTIONS = QuestionSet(
    name="tiny",
    source="hand labels",
    questions=[
        AskQuestion(
            id="Q1",
            question="How many?",
            truth="5 then 1",
            numbers=[
                ExpectedNumber(name="first", truth=5, abs_tol=1),
                ExpectedNumber(name="second", truth=1, abs_tol=1),
            ],
        ),
        AskQuestion(id="Q2", question="Describe.", truth="not graded"),
    ],
)


def test_a_benchmark_grades_every_run() -> None:
    api = FakeApi(
        {
            "How many?": [answer([5, 1]), answer([7, True], 30.0), "HTTP 502"],
            "Describe.": [answer([]), answer([]), answer([])],
        }
    )
    bench = run_benchmark(api, QUESTIONS, runs=3, api_url="http://x", generated_at=NOW)

    assert api.asked[0] == ("How many?", ["first", "second"])
    assert api.asked[1] == ("Describe.", [])
    q1, q2 = bench.questions
    assert (q1.runs, q1.passed_runs, q1.pass_rate) == (3, 1, 0.333)
    assert q1.values == {"first": [5.0, 7.0, None], "second": [1.0, None, None]}
    assert q1.median_seconds == 20.0
    assert (q2.graded, q2.pass_rate) == (False, None)
    assert (bench.numbers_graded, bench.numbers_passed) == (6, 2)
    assert bench.runs_per_question == 3
    failed = [r for r in bench.runs if r.error]
    assert failed[0].error == "HTTP 502"
    assert failed[0].steps == 0


def test_only_runs_the_chosen_questions(tmp_path: Path) -> None:
    api = FakeApi({"Describe.": [answer([])]})
    bench = run_benchmark(api, QUESTIONS, runs=1, api_url="http://x", generated_at=NOW, only=["Q2"])
    assert [q.id for q in bench.questions] == ["Q2"]
    json_path, md_path = save_benchmark(bench, tmp_path / "out")
    assert json.loads(json_path.read_text("utf-8"))["name"] == "tiny"
    report = md_path.read_text("utf-8")
    assert report == render_markdown(bench)
    assert "not graded" in report
    assert "Graded numbers within tolerance: 0 of 0." in report


def sse_body(*events: tuple[str, dict[str, object]]) -> bytes:
    blocks = [": keep-alive\n\n"] + [f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events]
    return "".join(blocks).encode()


def http_api(handler: object, passphrase: str | None = None) -> HttpAskApi:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.Client(base_url="https://ui.example/rulecam-api", transport=transport)
    return HttpAskApi(
        "https://ui.example/rulecam-api", passphrase=passphrase, origin=None, http=http
    )


def test_http_api_logs_in_once_and_reads_the_answer_event() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"owner": True, "csrf_token": "tok"})
        body = sse_body(("step", {"type": "step"}), ("answer", {"answer": answer([3])}))
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    api = http_api(handler, passphrase="secret")  # noqa: S106 - a test value
    assert api.ask("q", ["n"])["numbers"] == [3]
    assert api.ask("q2", [])["numbers"] == [3]
    api.close()

    assert [r.url.path for r in seen] == [
        "/rulecam-api/auth/login",
        "/rulecam-api/ask/stream",
        "/rulecam-api/ask/stream",
    ]
    assert seen[0].headers["Origin"] == "https://ui.example"
    assert seen[1].headers["X-CSRF-Token"] == "tok"
    assert json.loads(seen[1].content) == {"question": "q", "numbers": ["n"]}


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(500, text="down"), "HTTP 500"),
        (httpx.Response(200, content=sse_body(("error", {"detail": "boom"}))), "boom"),
        (httpx.Response(200, content=sse_body(("answer", {"answer": None}))), "no answer"),
        (httpx.Response(200, content=sse_body(("step", {}))), "ended without"),
    ],
)
def test_http_api_failures_are_run_errors(response: httpx.Response, message: str) -> None:
    api = http_api(lambda request: response)
    with pytest.raises(EvalRunError, match=message):
        api.ask("q", [])


def test_http_api_login_and_transport_failures() -> None:
    refused = http_api(lambda request: httpx.Response(401, text="no"), passphrase="bad")  # noqa: S106 - a test value
    with pytest.raises(EvalRunError, match="owner login: HTTP 401"):
        refused.ask("q", [])

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(EvalRunError, match="ConnectError"):
        http_api(broken).ask("q", [])


def test_the_cli_runs_the_benchmark_and_writes_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class Api(FakeApi):
        def __init__(self, base_url: str, **kwargs: object) -> None:
            super().__init__({"How many?": [answer([5, 1])], "Describe.": [answer([])]})
            calls.append(base_url)

        def close(self) -> None:
            calls.append("closed")

    monkeypatch.setattr(ask_module, "HttpAskApi", Api)
    questions = tmp_path / "q.yaml"
    questions.write_text(json.dumps(QUESTIONS.model_dump(mode="json")), "utf-8")
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "ask",
            str(questions),
            "--runs",
            "1",
            "--api",
            "http://api",
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == ["http://api", "closed"]
    assert "Graded numbers within tolerance: 2 of 2." in result.output
    assert len(list(tmp_path.glob("*-tiny.json"))) == 1

    bad = CliRunner().invoke(app, ["eval", "ask", str(tmp_path / "missing.yaml")])
    assert bad.exit_code == 2
