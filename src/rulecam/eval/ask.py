"""Ask benchmark (`rulecam eval ask`): ask a question set of a running API, score the numbers.

A question set (YAML, e.g. `eval/ask/corridor.yaml`) lists owner questions and, where a labelled
truth exists, the numbers the answer must give, each with its truth and tolerance. Every
question is asked `runs` times through `POST /ask/stream` (so a long answer survives proxies),
naming the numbers it wants (`numbers` in the request). The agent returns them in the answer's
structured `numbers` field, in that order, so grading never parses prose. Questions without
numbers are asked and recorded, not graded.

The report gives each question's pass rate over the runs, every run's values (how stable the
answers are), and the median seconds; overall, the graded numbers that passed.

Owner routes accept the API's own machine (loopback) or an owner session: with a passphrase,
the client logs in (`POST /auth/login`) and sends the session's CSRF token and an allowed
`Origin` with each question.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import urlsplit

import httpx
import yaml
from pydantic import Field, ValidationError, model_validator

from rulecam.contracts import Frozen
from rulecam.eval.errors import EvalRunError, EvalSetError
from rulecam.log import get_logger

_log = get_logger(__name__)

STREAM_PATH: Final = "/ask/stream"
LOGIN_PATH: Final = "/auth/login"
CSRF_HEADER: Final = "X-CSRF-Token"
READ_TIMEOUT_S: Final = 120.0  # the API sends a keep-alive every 10 s while it works
CONNECT_TIMEOUT_S: Final = 15.0


class ExpectedNumber(Frozen):
    """A number the answer must give: what it is (sent to the API), its truth and tolerance.

    Attributes:
        name: What the number is, in words (the agent fills `numbers` in this order).
        truth: The labelled value.
        abs_tol: Allowed absolute error.
        rel_tol: Allowed error as a fraction of the truth (the larger of the two applies).
    """

    name: str = Field(min_length=1, max_length=200)
    truth: float
    abs_tol: float = Field(default=0.0, ge=0.0)
    rel_tol: float = Field(default=0.0, ge=0.0)

    @property
    def tolerance(self) -> float:
        """The allowed error."""
        return max(self.abs_tol, self.rel_tol * abs(self.truth))


class AskQuestion(Frozen):
    """One question of the set.

    Attributes:
        id: Short id (`C01`).
        question: The owner's words, sent as they are.
        numbers: What to grade (empty: asked and recorded only).
        truth: The truth in words, for the report.
    """

    id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=2000)
    numbers: list[ExpectedNumber] = Field(default_factory=list, max_length=12)
    truth: str = ""


class QuestionSet(Frozen):
    """A named set of questions with their truths and where the truths come from."""

    name: str
    source: str = ""
    notes: str = ""
    questions: list[AskQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> QuestionSet:
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            msg = "question ids must be unique"
            raise ValueError(msg)
        return self


def load_question_set(path: Path) -> QuestionSet:
    """Read and validate a question set.

    Raises:
        EvalSetError: When the file is missing or malformed.
    """
    try:
        return QuestionSet.model_validate(yaml.safe_load(path.read_text("utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        msg = f"{path}: not a valid Ask question set: {exc}"
        raise EvalSetError(msg) from exc


class AskApi(Protocol):
    """What the benchmark needs from the API: one answer, as `AskAnswer` JSON."""

    def ask(self, question: str, numbers: Sequence[str]) -> dict[str, object]:
        """Ask one question, naming the numbers wanted; raise `EvalRunError` on failure."""
        ...


def sse_events(lines: Iterator[str]) -> Iterator[tuple[str, str]]:
    """`(event, data)` for each server-sent event in a stream of lines (comments skipped)."""
    event, data = "message", list[str]()
    for line in lines:
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        yield event, "\n".join(data)


class HttpAskApi:
    """`AskApi` over HTTP: `POST /ask/stream`, logged in as the owner when given a passphrase."""

    def __init__(
        self,
        base_url: str,
        *,
        passphrase: str | None = None,
        origin: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        """Connect to the API.

        Args:
            base_url: The API (`http://localhost:8000`), or the UI's proxy
                (`https://rulecam.vercel.app/rulecam-api`).
            passphrase: The owner passphrase, to log in when not on the API's machine.
            origin: The `Origin` a session's writes carry (default: the base URL's origin).
            http: An httpx client to use instead (tests).
        """
        timeout = httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        parts = urlsplit(base_url)
        self._origin = origin or f"{parts.scheme}://{parts.netloc}"
        self._passphrase = passphrase
        self._headers: dict[str, str] | None = None

    def close(self) -> None:
        """Close the connection pool."""
        self._http.close()

    def _session(self) -> dict[str, str]:
        """Headers for owner writes: none on loopback, else a fresh session's CSRF and Origin."""
        if self._headers is not None:
            return self._headers
        if self._passphrase is None:
            self._headers = {}
            return self._headers
        reply = self._http.post(
            LOGIN_PATH, json={"passphrase": self._passphrase}, headers={"Origin": self._origin}
        )
        if reply.is_error:
            msg = f"owner login: HTTP {reply.status_code} {reply.text[:200]}"
            raise EvalRunError(msg)
        token = reply.json().get("csrf_token")
        self._headers = {"Origin": self._origin, CSRF_HEADER: str(token or "")}
        return self._headers

    def ask(self, question: str, numbers: Sequence[str]) -> dict[str, object]:
        """See `AskApi.ask`."""
        body = {"question": question, "numbers": list(numbers)}
        headers = {"Accept": "text/event-stream", **self._session()}
        try:
            with self._http.stream("POST", STREAM_PATH, json=body, headers=headers) as reply:
                if reply.is_error:
                    reply.read()
                    msg = f"ask: HTTP {reply.status_code} {reply.text[:300]}"
                    raise EvalRunError(msg)
                for event, data in sse_events(reply.iter_lines()):
                    found = _answer_of(event, data)
                    if found is not None:
                        return found
        except httpx.HTTPError as exc:
            msg = f"ask: {type(exc).__name__}: {exc}"
            raise EvalRunError(msg) from exc
        msg = "ask: the stream ended without an answer"
        raise EvalRunError(msg)


def _answer_of(event: str, data: str) -> dict[str, object] | None:
    """The answer of an `answer` event; raises on an `error` event; `None` for the others."""
    if event == "error":
        detail = json.loads(data).get("detail") or "answering failed"
        raise EvalRunError(f"ask: {detail}")
    if event != "answer":
        return None
    answer = json.loads(data).get("answer")
    if not isinstance(answer, dict):
        msg = "ask: the answer event has no answer"
        raise EvalRunError(msg)
    return answer


class NumberScore(Frozen):
    """One graded number of one run."""

    name: str
    truth: float
    tolerance: float
    value: float | None
    error: float | None
    passed: bool


class QuestionRun(Frozen):
    """One run of one question: the answer and its graded numbers."""

    id: str
    run: int
    answer: str = ""
    numbers: list[NumberScore] = Field(default_factory=list)
    seconds: float | None = None
    steps: int = 0
    models: list[str] = Field(default_factory=list)
    receipt: str = ""
    error: str | None = None

    @property
    def passed(self) -> bool:
        """Whether it answered and every graded number is within tolerance."""
        return self.error is None and all(n.passed for n in self.numbers)


class QuestionScore(Frozen):
    """A question over all its runs."""

    id: str
    question: str
    truth: str
    graded: bool
    runs: int
    passed_runs: int
    pass_rate: float | None
    values: dict[str, list[float | None]]
    median_seconds: float | None


class AskBenchmark(Frozen):
    """The whole benchmark: per-question scores, every run, and the overall numbers."""

    name: str
    source: str
    api: str
    generated_at: datetime
    runs_per_question: int
    numbers_graded: int
    numbers_passed: int
    median_seconds: float | None
    questions: list[QuestionScore]
    runs: list[QuestionRun]


def score_number(expected: ExpectedNumber, value: float | None) -> NumberScore:
    """Grade one number against its truth."""
    error = None if value is None else value - expected.truth
    return NumberScore(
        name=expected.name,
        truth=expected.truth,
        tolerance=expected.tolerance,
        value=value,
        error=error,
        passed=error is not None and abs(error) <= expected.tolerance + 1e-9,
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def run_question(api: AskApi, question: AskQuestion, run: int) -> QuestionRun:
    """Ask once and grade the answer's numbers (a failed ask is a failed run, not an abort)."""
    try:
        answer = api.ask(question.question, [n.name for n in question.numbers])
    except EvalRunError as exc:
        _log.warning("ask_benchmark_failed", id=question.id, run=run, error=str(exc))
        missing = [score_number(n, None) for n in question.numbers]
        return QuestionRun(id=question.id, run=run, numbers=missing, error=str(exc))
    given = answer.get("numbers")
    values = list(given) if isinstance(given, list) else []
    numbers = [
        score_number(n, _number(values[k]) if k < len(values) else None)
        for k, n in enumerate(question.numbers)
    ]
    steps = answer.get("steps")
    models = answer.get("models")
    return QuestionRun(
        id=question.id,
        run=run,
        answer=str(answer.get("answer", "")),
        numbers=numbers,
        seconds=_number(answer.get("seconds")),
        steps=len(steps) if isinstance(steps, list) else 0,
        models=[str(m) for m in models] if isinstance(models, list) else [],
        receipt=str(answer.get("receipt", "")),
    )


def summarise(
    questions: QuestionSet, runs: Sequence[QuestionRun], *, api: str, generated_at: datetime
) -> AskBenchmark:
    """Score every question over its runs."""
    scores = []
    for question in questions.questions:
        mine = [r for r in runs if r.id == question.id]
        graded = bool(question.numbers)
        passed = sum(r.passed for r in mine) if graded else 0
        timed = [r.seconds for r in mine if r.seconds is not None]
        scores.append(
            QuestionScore(
                id=question.id,
                question=question.question,
                truth=question.truth,
                graded=graded,
                runs=len(mine),
                passed_runs=passed,
                pass_rate=round(passed / len(mine), 3) if graded and mine else None,
                values={
                    n.name: [r.numbers[k].value for r in mine]
                    for k, n in enumerate(question.numbers)
                },
                median_seconds=round(statistics.median(timed), 1) if timed else None,
            )
        )
    graded_numbers = [n for r in runs for n in r.numbers]
    timed_all = [r.seconds for r in runs if r.seconds is not None]
    return AskBenchmark(
        name=questions.name,
        source=questions.source,
        api=api,
        generated_at=generated_at,
        runs_per_question=max((r.run for r in runs), default=0),
        numbers_graded=len(graded_numbers),
        numbers_passed=sum(n.passed for n in graded_numbers),
        median_seconds=round(statistics.median(timed_all), 1) if timed_all else None,
        questions=scores,
        runs=list(runs),
    )


def run_benchmark(
    api: AskApi,
    questions: QuestionSet,
    *,
    runs: int,
    api_url: str,
    generated_at: datetime,
    only: Sequence[str] = (),
) -> AskBenchmark:
    """Ask every question (or only the ids in `only`) `runs` times, then score them.

    Questions are asked one at a time, run after run, so each run sees the whole set once.
    """
    chosen = [q for q in questions.questions if not only or q.id in only]
    done = [run_question(api, q, run) for run in range(1, runs + 1) for q in chosen]
    subset = questions.model_copy(update={"questions": chosen})
    return summarise(subset, done, api=api_url, generated_at=generated_at)


def _value(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def render_markdown(benchmark: AskBenchmark) -> str:
    """The report: overall result, a table per question, then each run's numbers."""
    lines = [
        f"# Ask benchmark: {benchmark.name} ({benchmark.generated_at:%Y-%m-%d %H:%M} UTC)",
        "",
        f"API: `{benchmark.api}`. Truth: {benchmark.source or 'see the question set'}. "
        f"Each question asked {benchmark.runs_per_question} times.",
        "",
        f"**Graded numbers within tolerance: {benchmark.numbers_passed} of "
        f"{benchmark.numbers_graded}.** Median answer time: "
        f"{_value(benchmark.median_seconds)} s.",
        "",
        "| # | Question | Truth | Values per run | Passed runs | Median s |",
        "|---|---|---|---|---|---|",
    ]
    for q in benchmark.questions:
        values = "; ".join(
            f"{name}: {', '.join(_value(v) for v in vals)}" for name, vals in q.values.items()
        )
        passed = f"{q.passed_runs}/{q.runs}" if q.graded else "not graded"
        lines.append(
            f"| {q.id} | {q.question} | {q.truth or '—'} | {values or '—'} | {passed} | "
            f"{_value(q.median_seconds)} |"
        )
    lines += ["", "## Runs", ""]
    for r in benchmark.runs:
        status = f"error: {r.error}" if r.error else ("pass" if r.passed else "fail")
        graded = ", ".join(
            f"{n.name} = {_value(n.value)} (truth {n.truth:g} ± {n.tolerance:g})" for n in r.numbers
        )
        lines.append(
            f"- {r.id} run {r.run}: {status}; {graded or 'not graded'}; "
            f"{_value(r.seconds)} s, {r.steps} steps; receipt `{r.receipt[:12] or '—'}`"
        )
    return "\n".join(lines) + "\n"


def save_benchmark(benchmark: AskBenchmark, out_dir: Path) -> tuple[Path, Path]:
    """Write the benchmark as JSON and Markdown into `out_dir`; return both paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{benchmark.generated_at:%Y-%m-%dT%H%M%SZ}-{benchmark.name}"
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    json_path.write_text(benchmark.model_dump_json(indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(benchmark), encoding="utf-8")
    return json_path, md_path
