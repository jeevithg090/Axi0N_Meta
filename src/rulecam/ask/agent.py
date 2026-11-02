"""Answer an owner's free-form question about what the cameras saw (`POST /ask`).

Muse Spark 1.3 runs a short tool loop. Each turn it returns an `AskTurn`: a list of actions, as
many as it decides (up to `max_actions`), which run at the same time in a thread pool:

- `sql`: read-only DuckDB statements over `track_points` / `track_spans` (`ask.data`), the
  runtime's SAM 3.1 tracks;
- `count_objects`: SAM 3.1 (Meta hosted) prompted with any noun phrase on the recorded frames of
  a camera at chosen instants;
- `look`: Muse Glimmer answers a question about the recorded frames at chosen instants;
- `answer`: the final answer (on its own).

The results go back to Spark in the order of its actions, as the next message. Spark decides
which tools to use, how many at once, what to count and how to read the numbers; nothing about
the question types is written here. The loop stops at `MAX_STEPS` turns, then asks for an answer
from what it has. Every step (turn, action, result, seconds, model) is returned with the answer.

Nothing is cut silently (`AskLimits`): a query returns up to `max_rows` rows and says how many
there were; a turn's results share `max_result_chars` characters and a cut result says what it
left out (`ask.results`); instants past `max_frames`, or with no recorded frame, are named, and
so are actions past `max_actions`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import yaml

from rulecam.ask.data import DEFAULT_MAX_ROWS, QueryResult, SqlError, TrackTables
from rulecam.ask.results import DEFAULT_MAX_RESULT_CHARS, fit_results
from rulecam.ask.trace import AskAction, AskAnswer, AskStep, AskTurn
from rulecam.ask.vision import (
    DEFAULT_MAX_FRAMES,
    Frame,
    FrameGrabber,
    count_objects,
    look,
    marked,
)
from rulecam.contracts import Clock, StructuredLLM
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter

_log = get_logger(__name__)

SPARK_MODEL: Final = "muse-spark-1.3"
SAM_MODEL: Final = "sam-3.1"
MAX_STEPS: Final = 8
DEFAULT_MAX_ACTIONS: Final = 6


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
    """A tool's result and the model that produced it (`None` for SQL)."""

    result: object
    model: str | None = None


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
4. answer: the final reply in the owner's language, with the numbers, the time range they cover \
and one short sentence on how you got them and how sure you are.

A result is cut only when it is too long, and then it says so ("note", "truncated", \
"total_rows", "rows_shown"): never read a cut result as complete; aggregate or page instead.

Use the owner's names for cameras and areas. Interpret times like "1 pm" in the site's time \
zone ({timezone}). If the recording does not cover the time asked about, say so and answer for \
what it covers.\
"""


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

    def ask(self, question: str) -> AskAnswer:
        """Answer one question.

        Args:
            question: The owner's question, in any language.

        Returns:
            The answer with its steps.
        """
        started = time.perf_counter()
        tables = self._tables()
        try:
            messages = self._opening(question, tables)
            steps: list[AskStep] = []
            for turn in range(MAX_STEPS + 1):
                last = turn == MAX_STEPS
                if last:
                    messages.append({"role": "user", "content": "Steps used up: answer now."})
                actions = self._next(messages)
                final = _final(actions, last=last)
                if final is not None:
                    action, reply = final
                    steps.append(AskStep(turn=turn, action=action, result=None, seconds=0.0))
                    return AskAnswer(
                        question=question,
                        answer=reply,
                        steps=steps,
                        seconds=round(time.perf_counter() - started, 1),
                        models=_models(steps),
                    )
                run = actions[: self._limits.max_actions]
                done = self._run_all(turn, run, tables)
                steps.extend(done)
                messages.append(
                    {"role": "assistant", "content": AskTurn(actions=run).model_dump_json()}
                )
                messages.append(
                    {"role": "user", "content": self._results(done, len(actions) - len(run))}
                )
        finally:
            tables.close()
        msg = "unreachable: the loop always answers on its last turn"
        raise AssertionError(msg)

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
                    answer="Sorry, I could not finish working that out. Please ask again.",
                )
            ]
        return turn.actions

    def _run_all(
        self, turn: int, actions: Sequence[AskAction], tables: TrackTables
    ) -> list[AskStep]:
        """Run a turn's actions at the same time; the steps come back in the actions' order."""
        if len(actions) == 1:
            return [self._step(turn, actions[0], tables)]
        with ThreadPoolExecutor(len(actions), thread_name_prefix="ask-tool") as pool:
            futures = [pool.submit(self._step, turn, action, tables) for action in actions]
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

    def _opening(self, question: str, tables: TrackTables) -> list[dict[str, object]]:
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
        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"Recorded data:\n{json.dumps(context)}\n\nOwner's question: {question}",
            },
        ]

    def _run(self, action: AskAction, tables: TrackTables) -> _Outcome:
        try:
            if action.action == "sql":
                return _Outcome(_sql_result(tables.run_sql(action.sql or "")))
            asked = _instants(action.times)
            used = asked[: self._limits.max_frames]
            frames = self._frames.frames(action.camera_id or "", used)
            notes = _frame_notes(asked, used, frames, self._limits.max_frames)
            if not frames:
                return _Outcome({"error": "no recorded frames at those instants on that camera"})
            if action.action == "count_objects":
                counts = count_objects(self._segmenter, frames, action.concept or "person")
                found = [c.model_dump(mode="json") for c in counts]
                return _Outcome({"frames": found, **_notes(notes)}, SAM_MODEL)
            labels: dict[str, str] = {}
            if action.marks and self._marks_dir is not None:
                frames = [self._mark(f, tables, labels) for f in frames]
            answer = look(self._glimmer, self._glimmer_model, frames, action.question or "")
            return _Outcome(
                {**answer.model_dump(), "marks": labels, **_notes(notes)}, self._glimmer_model
            )
        except (SqlError, ValueError, RulecamError, OSError) as exc:
            _log.info("ask_step_failed", action=action.action, error=str(exc))
            return _Outcome({"error": str(exc)})

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


def _final(actions: Sequence[AskAction], *, last: bool) -> tuple[AskAction, str] | None:
    """The answer action and its text, when the turn answers (or must, being the last)."""
    for action in actions:
        if action.action == "answer":
            return action, action.answer or action.thought
    if last:
        first = actions[0]
        return first, first.answer or first.thought
    return None


def _models(steps: Sequence[AskStep]) -> list[str]:
    """Spark, then every other model in the order the steps first used it."""
    models = [SPARK_MODEL]
    for step in steps:
        if step.model is not None and step.model not in models:
            models.append(step.model)
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
