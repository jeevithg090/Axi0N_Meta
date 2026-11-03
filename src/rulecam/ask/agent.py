"""Answer an owner's free-form question about what the cameras saw (`POST /ask`).

Muse Spark 1.3 runs a short tool loop. Each turn it returns an `AskTurn`: a list of actions, as
many as it decides (up to `max_actions`), which run at the same time in a thread pool:

- `sql`: read-only DuckDB statements over `track_points` / `track_spans` (`ask.data`), the
  runtime's SAM 3.1 tracks;
- `count_objects`: SAM 3.1 (Meta hosted) prompted with any noun phrase on the recorded frames of
  a camera at chosen instants;
- `look`: Muse Glimmer answers a question about the recorded frames at chosen instants;
- `watch`: Muse Spark watches the recorded clip of a chosen window as video (`ask.watch`), with
  Glimmer on evenly spaced stills as the fallback when the video call fails;
- `answer`: the final answer (on its own).

The results go back to Spark in the order of its actions, as the next message. Spark decides
which tools to use, how many at once, what to count and how to read the numbers; nothing about
the question types is written here. The loop stops at `MAX_STEPS` turns, then asks for an answer
from what it has. Every step (turn, action, result, seconds, model, frames) is returned with the
answer, and each is also sent as an `AskEvent` the moment it happens (`POST /ask/stream`).

With a reviewer (add-on 2.7, `ask.review`), Muse Spark Open checks each draft answer against
the trace and the conversation; a draft it sends back goes to Spark once with its findings, and
Spark gets up to `REVIEW_STEPS` more turns to work on it and answer again. Every review is kept
on the answer (`reviews`) and streamed (`reviewing`, `review`).

A follow-up names the conversation it continues (`conversation_id`): the earlier questions,
answers and steps of that conversation (kept in memory for the last `MAX_CONVERSATIONS`) go to
Spark with the fresh data.

Nothing is cut silently (`AskLimits`): a query returns up to `max_rows` rows and says how many
there were; a turn's results share `max_result_chars` characters and a cut result says what it
left out (`ask.results`); instants past `max_frames`, or with no recorded frame, are named, and
so are actions past `max_actions`.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import yaml

from rulecam.ask.data import DEFAULT_MAX_ROWS, QueryResult, SqlError, TrackTables
from rulecam.ask.results import DEFAULT_MAX_RESULT_CHARS, fit_results
from rulecam.ask.review import REVIEW_PROMPT, AnswerReviewer, AnswerVerdict, sent_back
from rulecam.ask.runlog import AskRunLog
from rulecam.ask.trace import (
    AskAction,
    AskAnswer,
    AskEvent,
    AskReview,
    AskStep,
    AskTurn,
    AskTurnTiming,
)
from rulecam.ask.vision import (
    DEFAULT_MAX_FRAMES,
    Frame,
    FrameGrabber,
    count_objects,
    look,
    marked,
)
from rulecam.ask.watch import (
    DEFAULT_MAX_WATCH_S,
    WATCH_MODEL,
    ClipJoiner,
    FileAttacher,
    spaced,
    watch,
)
from rulecam.contracts import Clock, StructuredLLM
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter

_log = get_logger(__name__)

SPARK_MODEL: Final = "muse-spark-1.3"
SAM_MODEL: Final = "sam-3.1"
MAX_STEPS: Final = 8
REVIEW_STEPS: Final = 3
DEFAULT_MAX_ACTIONS: Final = 6
MAX_CONVERSATIONS: Final = 64
STOPPED: Final = "Stopped before an answer: the request went away."
APOLOGY: Final = "Sorry, I could not finish working that out. Please ask again."
_FRAME_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\.jpg$")

Emit = Callable[[AskEvent], None]
"""Receives each `AskEvent` as it happens (called from worker threads too)."""


@dataclass(frozen=True)
class AskLimits:
    """How much of each tool's output reaches Spark (settings `RULECAM_ASK_MAX_*`).

    Attributes:
        max_rows: Rows a SQL query returns at most (the full count is always reported).
        max_result_chars: Characters of tool results per turn, shared by the turn's actions.
        max_frames: Instants one `count_objects` or `look` call uses at most.
        max_actions: Tool calls one turn runs at once; the rest are reported as not run.
    """

    max_rows: int = DEFAULT_MAX_ROWS
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS
    max_frames: int = DEFAULT_MAX_FRAMES
    max_actions: int = DEFAULT_MAX_ACTIONS


DEFAULT_LIMITS: Final = AskLimits()


@dataclass(frozen=True)
class _Outcome:
    """A tool's result, the model that produced it (`None` for SQL) and the frames it saw."""

    result: object
    model: str | None = None
    frames: tuple[Path, ...] = ()


@dataclass
class _Trace:
    """What one answer did so far: its steps, Spark's turns and the reviews of its drafts."""

    steps: list[AskStep] = field(default_factory=list)
    turns: list[AskTurnTiming] = field(default_factory=list)
    reviews: list[AskReview] = field(default_factory=list)


def _ignore(event: AskEvent) -> None:
    """The default `Emit`: nobody is listening."""


SYSTEM_PROMPT: Final = """\
You answer a site owner's questions about what their cameras recorded. The owner may ask \
anything: counts at a time, busiest periods, how long people stayed, queues, carts, baskets, \
staff, what someone was doing, what happened. Decide yourself how to find the answer with these \
tools, check a number a second way when you can, then answer. Each turn, reply with one JSON \
object holding a list of actions: up to {max_actions} tool calls, which all run at the same time \
(so none can use another's result; put checks that do not depend on each other in one turn), \
or one answer action on its own.

The recorded data (all times are TIMESTAMP in UTC without a zone; write literals as \
TIMESTAMP '2026-11-01 13:00:00'):
- track_points(track_id, camera_id, concept, ts, frame_idx, x1, y1, x2, y2, foot_px_x, \
foot_px_y, foot_m_x, foot_m_y, conf): the object memory. One row per tracked object per \
keyframe (keyframes_per_s in the data stats); boxes in pixels of a {width}x{height} frame, the \
foot point is the bottom centre, foot_m_* in metres when the camera is calibrated. Each concept \
is a separate SAM 3.1 prompt and concepts can overlap: a cashier can also be tracked as a \
person. One real object can be split into several track_ids (occlusion, re-entering), so \
distinct track_id counts over a period are an upper bound; the count present at one ts is \
reliable. To ask about an area (a lane, a door, a shelf), find its box with count_objects or \
look and filter on the foot point yourself.
- track_spans(track_id, camera_id, concept, first_seen, last_seen, seconds, points): one row per \
track.
- track_identity(track_id, camera_id, concept, identity_id, similarity, threshold): track \
fragments joined into identities by appearance (DINOv2 re-identification of each track's \
crops; similarity is how alike the fragment was to its identity, threshold the bar learned \
from objects seen at the same time). identity_spans(identity_id, camera_id, concept, \
first_seen, last_seen, seconds, fragments, seen_seconds) summarises each identity. Use \
identities, not track_ids, for unique visitors and dwell time; tracks still being recorded \
have no identity yet.
- identity_notes(identity_id, role, description, carrying): Muse Glimmer's note on each \
person identity (customer or staff, clothing, what they carry).
- captions(camera_id, t0, t1, caption, people, events): the temporal memory. What Muse Glimmer \
saw on each camera every {caption_s}; search them with ILIKE and read them for "what happened" \
and summary questions.

Tools:
1. sql: one or more DuckDB SELECT/WITH statements over those tables, separated by ';' (each \
gets its own result); up to {max_rows} rows come back per statement.
2. count_objects: SAM 3.1 finds any noun phrase you choose ("shopping cart", "cashier in black \
uniform", "checkout counter") on the recorded frames of camera_id at up to {max_frames} \
instants (times); returns the count and boxes [x1, y1, x2, y2, confidence] per frame.
3. look: Muse Glimmer looks at the recorded frames of camera_id at up to {max_frames} instants \
and answers your question in words (and a count if asked). With marks (default) the tracked \
objects are drawn as numbered boxes and the result maps each number to its track_id, so you can \
join what Glimmer says to the tables.
4. watch: Muse Spark watches the recorded video of camera_id from times[0] to times[1] (one \
clip of up to {max_watch_s} s) and answers your question, with the moments it relies on as UTC \
instants. Stills miss what only shows as movement: someone entering or leaving a shop, people \
walking together, a queue forming; watch sees it.
5. answer: the final reply in the owner's language, with the numbers, the time range they cover \
and one short sentence on how you got them and how sure you are. Also list the numbers the \
reply gives in `numbers`, in the order the question asks for them.

A result is cut only when it is too long, and then it says so ("note", "truncated", \
"total_rows", "rows_shown"): never read a cut result as complete; aggregate or page instead.

Use the owner's names for cameras and areas. Interpret times like "1 pm" in the site's time \
zone ({timezone}). If the recording does not cover the time asked about, say so and answer for \
what it covers.\
"""


def prompt_digest(*parts: str) -> str:
    """SHA-256 of the instructions the models get: the prompts, then the reply schemas."""
    schemas = [
        json.dumps(model.model_json_schema(), sort_keys=True) for model in (AskTurn, AnswerVerdict)
    ]
    return hashlib.sha256("\n\n".join([*parts, *schemas]).encode("utf-8")).hexdigest()


PROMPT_SHA256: Final = prompt_digest(SYSTEM_PROMPT, REVIEW_PROMPT)


class AskAgent:
    """Spark-driven question answering over the track store and the recorded video."""

    def __init__(
        self,
        *,
        spark: StructuredLLM,
        segmenter: ImageSegmenter,
        glimmer: StructuredLLM,
        glimmer_model: str,
        tables: Callable[[], TrackTables],
        frames: FrameGrabber,
        clock: Clock,
        timezone: str = "UTC",
        frame_size: tuple[int, int] = (1280, 720),
        site_profile: str = "",
        camera_names: dict[str, str] | None = None,
        camera_profiles: Callable[[], dict[str, list[dict[str, object]]]] = dict,
        caption_every_s: float = 0.0,
        marks_dir: Path | None = None,
        limits: AskLimits = DEFAULT_LIMITS,
        runs: AskRunLog | None = None,
        reviewer: AnswerReviewer | None = None,
        clips: ClipJoiner | None = None,
        files: FileAttacher | None = None,
        watch_model: str = WATCH_MODEL,
    ) -> None:
        """Create the agent.

        Args:
            spark: Muse Spark 1.3 (plans the steps and answers).
            segmenter: SAM 3.1 on single images (Meta hosted).
            glimmer: Muse Glimmer (looks at frames).
            glimmer_model: Glimmer's model name.
            tables: Opens a fresh snapshot of the track store.
            frames: Decodes recorded frames.
            clock: Time source (the current time is given to Spark).
            timezone: The site's time zone name, for times like "1 pm".
            frame_size: The cameras' frame size, for reading boxes.
            site_profile: The owner's description of the site (`RULECAM_SITE_PROFILE`).
            camera_names: Camera id -> the owner's name for it (`RULECAM_CAMERA_NAMES`).
            camera_profiles: Camera id -> what the planner's survey saw there.
            caption_every_s: How often the caption memory writes (0: off).
            marks_dir: Where frames with numbered boxes are written.
            limits: How much tool output reaches Spark (the snapshot's row cap is set where
                `tables` is built, see `open_tables`).
            runs: The run log each answer is appended to (its row hash is the receipt).
            reviewer: A second model that checks each draft and can send it back once.
            clips: Joins recorded chunks into one clip for `watch` (`None`: watch unavailable).
            files: Uploads a clip for Spark (`SparkClient.attach`; `None`: watch unavailable).
            watch_model: The Spark model that takes video.
        """
        self._spark = spark
        self._segmenter = segmenter
        self._glimmer = glimmer
        self._glimmer_model = glimmer_model
        self._tables = tables
        self._frames = frames
        self._clock = clock
        self._timezone = timezone
        self._frame_size = frame_size
        self._site_profile = site_profile
        self._camera_names = dict(camera_names or {})
        self._camera_profiles = camera_profiles
        self._caption_every_s = caption_every_s
        self._marks_dir = marks_dir
        self._limits = limits
        self._runs = runs
        self._reviewer = reviewer
        self._clips = clips
        self._files = files
        self._watch_model = watch_model
        self._conversations: OrderedDict[str, list[AskAnswer]] = OrderedDict()
        self._lock = threading.Lock()

    def ask(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        numbers: Sequence[str] = (),
        emit: Emit = _ignore,
        stop: threading.Event | None = None,
    ) -> AskAnswer:
        """Answer one question.

        Args:
            question: The owner's question, in any language.
            conversation_id: The conversation a follow-up continues; `None` starts one.
            numbers: Names of the numbers the answer must give, in order (benchmarks).
            emit: Receives each step as it happens.
            stop: When set, no further turn starts (the asker went away).

        Returns:
            The answer with its steps, numbers and run-log receipt.
        """
        started = time.perf_counter()
        conversation = conversation_id or uuid.uuid4().hex
        emit(AskEvent(type="start", conversation_id=conversation))
        earlier = self._earlier(conversation)
        trace = _Trace()
        tables = self._tables()
        try:
            messages = self._opening(question, tables, earlier, numbers)
            final = self._loop(messages, tables, trace, emit, stop, steps=MAX_STEPS)
            final = self._reviewed(
                question,
                final,
                messages=messages,
                tables=tables,
                trace=trace,
                earlier=earlier,
                emit=emit,
                stop=stop,
            )
        finally:
            tables.close()
        answer = self._logged(
            AskAnswer(
                question=question,
                answer=final.answer or final.thought,
                steps=trace.steps,
                seconds=round(time.perf_counter() - started, 1),
                models=_models(trace.steps, trace.reviews),
                conversation_id=conversation,
                turns=trace.turns,
                numbers=final.numbers,
                prompt_sha256=PROMPT_SHA256,
                reviews=trace.reviews,
            )
        )
        self._remember(answer)
        emit(AskEvent(type="answer", conversation_id=conversation, answer=answer))
        return answer

    def _reviewed(
        self,
        question: str,
        final: AskAction,
        *,
        messages: list[dict[str, object]],
        tables: TrackTables,
        trace: _Trace,
        earlier: Sequence[AskAnswer],
        emit: Emit,
        stop: threading.Event | None,
    ) -> AskAction:
        """The answer after the reviewer's check: the draft, or Spark's answer once sent back."""
        if self._reviewer is None or final.answer in (STOPPED, APOLOGY):
            return final
        if stop is not None and stop.is_set():
            return final
        turn = trace.turns[-1].turn if trace.turns else 0
        emit(AskEvent(type="reviewing", turn=turn))
        review = self._reviewer.review(question, final, trace.steps, earlier)
        review = review.model_copy(update={"turn": turn})
        trace.reviews.append(review)
        emit(AskEvent(type="review", turn=turn, review=review))
        if review.verdict != "revise":
            return final
        messages.append(
            {"role": "assistant", "content": AskTurn(actions=[final]).model_dump_json()}
        )
        messages.append({"role": "user", "content": sent_back(review)})
        return self._loop(messages, tables, trace, emit, stop, steps=REVIEW_STEPS)

    def _logged(self, answer: AskAnswer) -> AskAnswer:
        """The answer with its run-log receipt (unchanged when there is no log or it fails)."""
        if self._runs is None:
            return answer
        try:
            receipt = self._runs.append(answer)
        except OSError as exc:
            _log.warning("ask_run_log_failed", error=repr(exc))
            return answer
        return answer.model_copy(update={"receipt": receipt})

    def frame_file(self, name: str) -> Path | None:
        """A frame a step showed a model, by its file name; `None` for anything else."""
        if not _FRAME_NAME.match(name):
            return None
        for folder in (self._frames.out_dir, self._marks_dir):
            path = folder / name if folder is not None else None
            if path is not None and path.is_file():
                return path
        return None

    def _loop(
        self,
        messages: list[dict[str, object]],
        tables: TrackTables,
        trace: _Trace,
        emit: Emit,
        stop: threading.Event | None,
        *,
        steps: int,
    ) -> AskAction:
        """Spark's turns until it answers (at the latest after `steps` tool turns)."""
        for k in range(steps + 1):
            if stop is not None and stop.is_set():
                return AskAction(thought=STOPPED, action="answer", answer=STOPPED)
            turn, last = len(trace.turns), k == steps
            if last:
                messages.append({"role": "user", "content": "Steps used up: answer now."})
            emit(AskEvent(type="thinking", turn=turn))
            started = time.perf_counter()
            actions = self._next(messages)
            seconds = round(time.perf_counter() - started, 1)
            trace.turns.append(
                AskTurnTiming(turn=turn, model=SPARK_MODEL, seconds=seconds, actions=len(actions))
            )
            emit(AskEvent(type="actions", turn=turn, actions=actions, seconds=seconds))
            final = _final(actions, last=last)
            if final is not None:
                trace.steps.append(AskStep(turn=turn, action=final, result=None, seconds=0.0))
                return final
            run = actions[: self._limits.max_actions]
            done = self._run_all(turn, run, tables, emit)
            trace.steps.extend(done)
            messages.append(
                {"role": "assistant", "content": AskTurn(actions=run).model_dump_json()}
            )
            messages.append(
                {"role": "user", "content": self._results(done, len(actions) - len(run))}
            )
        msg = "unreachable: the loop always answers on its last turn"
        raise AssertionError(msg)

    def _earlier(self, conversation: str) -> list[AskAnswer]:
        with self._lock:
            return list(self._conversations.get(conversation, []))

    def _remember(self, answer: AskAnswer) -> None:
        with self._lock:
            past = self._conversations.pop(answer.conversation_id, [])
            self._conversations[answer.conversation_id] = [*past, answer]
            while len(self._conversations) > MAX_CONVERSATIONS:
                self._conversations.popitem(last=False)

    def _next(self, messages: list[dict[str, object]]) -> list[AskAction]:
        """Spark's next actions; a reply that is not a valid turn ends with an apology."""
        try:
            turn = self._spark.structured(model=SPARK_MODEL, messages=messages, schema=AskTurn)
        except RulecamError as exc:
            _log.warning("ask_spark_failed", error=str(exc)[:200])
            return [
                AskAction(
                    thought=f"Muse Spark did not return a valid step ({type(exc).__name__}).",
                    action="answer",
                    answer=APOLOGY,
                )
            ]
        return turn.actions

    def _run_all(
        self, turn: int, actions: Sequence[AskAction], tables: TrackTables, emit: Emit
    ) -> list[AskStep]:
        """Run a turn's actions at the same time; the steps come back in the actions' order."""

        def job(index: int, action: AskAction) -> AskStep:
            step = self._step(turn, action, tables)
            emit(AskEvent(type="step", turn=turn, index=index, step=step))
            return step

        if len(actions) == 1:
            return [job(0, actions[0])]
        with ThreadPoolExecutor(len(actions), thread_name_prefix="ask-tool") as pool:
            futures = [pool.submit(job, k, action) for k, action in enumerate(actions)]
            return [future.result() for future in futures]

    def _step(self, turn: int, action: AskAction, tables: TrackTables) -> AskStep:
        started = time.perf_counter()
        outcome = self._run(action, tables)
        return AskStep(
            turn=turn,
            action=action,
            result=outcome.result,
            seconds=round(time.perf_counter() - started, 1),
            model=outcome.model,
            frames=[path.name for path in outcome.frames],
        )

    def _results(self, steps: Sequence[AskStep], not_run: int) -> str:
        """The next message: each result in the actions' order, within the turn's budget."""
        texts = fit_results([s.result for s in steps], self._limits.max_result_chars)
        if len(steps) == 1 and not not_run:
            return "Result:\n" + texts[0]
        lines = ["Results, in the order of your actions:"]
        lines += [
            f"[{k}] {step.action.action}: {text}"
            for k, (step, text) in enumerate(zip(steps, texts, strict=True), 1)
        ]
        if not_run:
            lines.append(
                f"Not run: your last {not_run} action(s) (at most {self._limits.max_actions} "
                "per turn); ask again if you still need them."
            )
        return "\n".join(lines)

    def _opening(
        self,
        question: str,
        tables: TrackTables,
        earlier: Sequence[AskAnswer] = (),
        numbers: Sequence[str] = (),
    ) -> list[dict[str, object]]:
        width, height = self._frame_size
        every = f"{self._caption_every_s:g} s" if self._caption_every_s else "(off: empty)"
        system = SYSTEM_PROMPT.format(
            width=width,
            height=height,
            timezone=self._timezone,
            caption_s=every,
            max_rows=self._limits.max_rows,
            max_frames=self._limits.max_frames,
            max_actions=self._limits.max_actions,
            max_watch_s=f"{self._clips.max_seconds if self._clips else DEFAULT_MAX_WATCH_S:g}",
        )
        video = {
            camera: {"first": first.isoformat(), "last": last.isoformat()}
            for camera, (first, last) in self._frames.cameras().items()
        }
        context = {
            "now_utc": self._clock.now().isoformat(),
            "site_timezone": self._timezone,
            "site_profile": self._site_profile or "not described by the owner",
            "camera_names": self._camera_names,
            "what_the_planner_saw_per_camera": self._camera_profiles(),
            "tracked_objects_per_camera": [c.model_dump(mode="json") for c in tables.cameras()],
            "data_stats": tables.stats(),
            "recorded_video_per_camera": video,
            "recent_captions": tables.recent_captions(),
        }
        before = ""
        if earlier:
            history = json.dumps(_history(earlier, self._limits.max_result_chars))
            before = (
                "\n\nEarlier in this conversation (the owner's new question may build on it; the "
                f"data above is newer):\n{history}"
            )
        wanted = ""
        if numbers:
            wanted = (
                "\n\nIn the answer action's `numbers`, give exactly these numbers in this order "
                f"(null for one you cannot get): {json.dumps(list(numbers))}"
            )
        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Recorded data:\n{json.dumps(context)}{before}\n\n"
                    f"Owner's question: {question}{wanted}"
                ),
            },
        ]

    def _run(self, action: AskAction, tables: TrackTables) -> _Outcome:
        try:
            if action.action == "sql":
                return _Outcome(_sql_result(tables.run_sql(action.sql or "")))
            if action.action == "watch":
                return self._watch(action)
            asked = _instants(action.times)
            used = asked[: self._limits.max_frames]
            frames = self._frames.frames(action.camera_id or "", used)
            notes = _frame_notes(asked, used, frames, self._limits.max_frames)
            if not frames:
                return _Outcome({"error": "no recorded frames at those instants on that camera"})
            shown = tuple(f.path for f in frames)
            if action.action == "count_objects":
                counts = count_objects(self._segmenter, frames, action.concept or "person")
                found = [c.model_dump(mode="json") for c in counts]
                return _Outcome({"frames": found, **_notes(notes)}, SAM_MODEL, shown)
            labels: dict[str, str] = {}
            if action.marks and self._marks_dir is not None:
                frames = [self._mark(f, tables, labels) for f in frames]
            answer = look(self._glimmer, self._glimmer_model, frames, action.question or "")
            return _Outcome(
                {**answer.model_dump(), "marks": labels, **_notes(notes)},
                self._glimmer_model,
                tuple(f.path for f in frames),
            )
        except (SqlError, ValueError, RulecamError, OSError) as exc:
            _log.info("ask_step_failed", action=action.action, error=str(exc))
            return _Outcome({"error": str(exc)})

    def _watch(self, action: AskAction) -> _Outcome:
        """Spark watches the window's clip; Glimmer looks at stills of it if that fails.

        Raises:
            ValueError: For a window that is not `[start, end]` or has nothing recorded.
            OSError: When the clip cannot be joined.
        """
        if self._clips is None or self._files is None:
            return _Outcome({"error": "watch is not available here (no video upload); use look"})
        window = _instants(action.times)
        if len(window) != 2:  # noqa: PLR2004 - a window is a start and an end
            msg = "watch needs times = [start, end]"
            raise ValueError(msg)
        camera = action.camera_id or ""
        clip = self._clips.clip(camera, window[0], window[1])
        about = {
            "camera_id": camera,
            "start": clip.start.isoformat(),
            "end": clip.end.isoformat(),
            "seconds": round(clip.seconds, 1),
            "chunks": clip.chunks,
        }
        question = action.question or "What happens in this clip?"
        try:
            seen = watch(self._spark, self._files, clip, question, model=self._watch_model)
        except (RulecamError, OSError, ValueError) as exc:
            _log.warning("ask_watch_failed", camera_id=camera, error=repr(exc)[:300])
            failed = f"{type(exc).__name__}: {str(exc)[:200]}"
        else:
            return _Outcome({"clip": about, **seen, **_notes(clip.notes)}, self._watch_model)
        instants = spaced(clip.start, clip.end, self._limits.max_frames)
        frames = self._frames.frames(camera, instants)
        if not frames:
            return _Outcome({"clip": about, "error": f"watching failed ({failed})"})
        answer = look(self._glimmer, self._glimmer_model, frames, question)
        fallback = (
            f"watching failed ({failed}); instead Glimmer looked at {len(frames)} evenly "
            "spaced stills of the window"
        )
        return _Outcome(
            {"clip": about, "fallback": fallback, **answer.model_dump(), **_notes(clip.notes)},
            self._glimmer_model,
            tuple(f.path for f in frames),
        )

    def _mark(self, frame: Frame, tables: TrackTables, labels: dict[str, str]) -> Frame:
        """The frame with every track near its instant drawn as a numbered box."""
        numbers = {track: label for label, track in labels.items()}
        marks = []
        for track_id, concept, box in tables.boxes_near(frame.camera_id, frame.at):
            if track_id not in numbers:
                numbers[track_id] = str(len(numbers) + 1)
                labels[numbers[track_id]] = track_id
            marks.append((numbers[track_id], concept, box))
        assert self._marks_dir is not None  # noqa: S101 - checked by the caller
        return marked(frame, marks, self._marks_dir)


def _final(actions: Sequence[AskAction], *, last: bool) -> AskAction | None:
    """The turn's answer action; on the last turn, its first action stands in for one."""
    for action in actions:
        if action.action == "answer":
            return action
    return actions[0] if last else None


def _models(steps: Sequence[AskStep], reviews: Sequence[AskReview] = ()) -> list[str]:
    """Spark, then every other model in the order the steps first used it, then the reviewer."""
    models = [SPARK_MODEL]
    for name in [s.model for s in steps] + [r.model for r in reviews]:
        if name and name not in models:
            models.append(name)
    return models


def _sql_result(results: Sequence[tuple[str, QueryResult | SqlError]]) -> object:
    """One statement's result as it is; several as `{"results": [...]}` with their SQL."""

    def one(result: QueryResult | SqlError) -> dict[str, object]:
        if isinstance(result, SqlError):
            return {"error": str(result)}
        return result.model_dump(mode="json", exclude_none=True)

    if len(results) == 1:
        return one(results[0][1])
    return {"results": [{"sql": sql, **one(result)} for sql, result in results]}


def _history(earlier: Sequence[AskAnswer], max_chars: int) -> list[dict[str, object]]:
    """Earlier questions, answers and steps of a conversation, within `max_chars` of results."""
    share = max_chars // max(1, len(earlier))
    out: list[dict[str, object]] = []
    for answer in earlier:
        tools = [s for s in answer.steps if s.action.action != "answer"]
        texts = fit_results([s.result for s in tools], share)
        steps = [
            {
                "turn": step.turn,
                "action": step.action.model_dump(exclude_defaults=True),
                "result": json.loads(text),
            }
            for step, text in zip(tools, texts, strict=True)
        ]
        out.append({"question": answer.question, "answer": answer.answer, "steps": steps})
    return out


def _notes(notes: Sequence[str]) -> dict[str, object]:
    return {"notes": list(notes)} if notes else {}


def _instants(times: Sequence[str]) -> list[datetime]:
    """Parse ISO-8601 instants (UTC when no offset is given); bad ones raise `ValueError`."""
    parsed = [datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times]
    return [t if t.tzinfo is not None else t.replace(tzinfo=UTC) for t in parsed]


def _frame_notes(
    asked: Sequence[datetime], used: Sequence[datetime], frames: Sequence[Frame], cap: int
) -> list[str]:
    """Say which asked instants were not looked at: past the frame cap, or not recorded."""
    notes = []
    if len(asked) > len(used):
        skipped = ", ".join(t.isoformat() for t in asked[len(used) :])
        notes.append(
            f"used the first {len(used)} of {len(asked)} instants (at most {cap} per call); "
            f"not looked at: {skipped}"
        )
    seen = {f.at for f in frames}
    missing = [t.isoformat() for t in used if t not in seen]
    if missing:
        notes.append(f"no recorded frame at: {', '.join(missing)}")
    return notes


def open_tables(
    root: Path,
    captions: Path | None = None,
    identities: Path | None = None,
    notes: Path | None = None,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> Callable[[], TrackTables]:
    """A factory of fresh snapshots of the track store at `root` and the memories' files."""
    return lambda: TrackTables(root, captions, identities, notes, max_rows=max_rows)


def camera_names(text: str) -> dict[str, str]:
    """Parse `id=name` pairs separated by commas (`RULECAM_CAMERA_NAMES`); blanks dropped."""
    pairs = (item.split("=", 1) for item in text.split(",") if "=" in item)
    return {key.strip(): value.strip() for key, value in pairs if key.strip() and value.strip()}


def planner_profiles(plans_dir: Path) -> Callable[[], dict[str, list[dict[str, object]]]]:
    """Camera id -> what the newest plan's survey saw there (concept, count, confidence)."""

    def read() -> dict[str, list[dict[str, object]]]:
        seen: dict[str, list[dict[str, object]]] = {}
        plans = sorted(plans_dir.glob("*.plan.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in plans:
            try:
                plan = yaml.safe_load(path.read_text("utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            found: dict[str, list[dict[str, object]]] = {}
            for item in plan.get("inventory") or []:
                row = {k: item.get(k) for k in ("concept", "count", "mean_confidence")}
                found.setdefault(str(item.get("camera_id", "")), []).append(row)
            for camera, items in found.items():
                seen.setdefault(camera, items)
        return seen

    return read
