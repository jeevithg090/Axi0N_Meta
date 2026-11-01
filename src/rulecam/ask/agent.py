"""Answer an owner's free-form question about what the cameras saw (`POST /ask`).

Muse Spark 1.3 runs a short tool loop. Each turn it returns one `AskAction`:

- `sql`: a read-only DuckDB query over `track_points` / `track_spans` (`ask.data`), the
  runtime's SAM 3.1 tracks;
- `count_objects`: SAM 3.1 (Meta hosted) prompted with any noun phrase on the recorded frames of
  a camera at chosen instants;
- `look`: Muse Glimmer answers a question about the recorded frames at chosen instants;
- `answer`: the final answer.

The tool's result goes back to Spark as the next message. Spark decides which tools to use, in
which order, what to count and how to read the numbers; nothing about the question types is
written here. The loop stops at `MAX_STEPS`, then asks for an answer from what it has. Every
step (action, result, seconds) is returned with the answer.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import Field

from rulecam.ask.data import SqlError, TrackTables
from rulecam.ask.vision import Frame, FrameGrabber, count_objects, look, marked
from rulecam.contracts import Clock, Frozen, StructuredLLM
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter

_log = get_logger(__name__)

SPARK_MODEL: Final = "muse-spark-1.3"
MAX_STEPS: Final = 8

Action = Literal["sql", "count_objects", "look", "answer"]


class AskAction(Frozen):
    """One turn of the model: a tool call or the final answer."""

    thought: str = Field(description="One sentence: why this step.")
    action: Action
    sql: str | None = Field(default=None, description="For sql: one SELECT or WITH statement.")
    concept: str | None = Field(default=None, description="For count_objects: a noun phrase.")
    camera_id: str | None = Field(default=None, description="For count_objects and look.")
    times: list[str] = Field(
        default_factory=list, description="For count_objects and look: ISO-8601 UTC instants."
    )
    question: str | None = Field(default=None, description="For look: what Glimmer should answer.")
    marks: bool = Field(
        default=True, description="For look: draw the tracked objects as numbered boxes."
    )
    answer: str | None = Field(default=None, description="For answer: the reply to the owner.")


class AskStep(Frozen):
    """What one step did, for the trace."""

    action: AskAction
    result: object
    seconds: float


class AskAnswer(Frozen):
    """The answer to an owner's question, with every step that led to it."""

    question: str
    answer: str
    steps: list[AskStep]
    seconds: float
    models: list[str]


SYSTEM_PROMPT: Final = """\
You answer a site owner's questions about what their cameras recorded. The owner may ask \
anything: counts at a time, busiest periods, how long people stayed, queues, carts, baskets, \
staff, what someone was doing, what happened. Decide yourself how to find the answer with these \
tools, check a number a second way when you can, then answer. Reply with exactly one JSON \
action per turn.

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
1. sql: one DuckDB SELECT/WITH query over those tables.
2. count_objects: SAM 3.1 finds any noun phrase you choose ("shopping cart", "cashier in black \
uniform", "checkout counter") on the recorded frames of camera_id at up to 6 instants (times); \
returns the count and boxes [x1, y1, x2, y2, confidence] per frame.
3. look: Muse Glimmer looks at the recorded frames of camera_id at up to 6 instants and answers \
your question in words (and a count if asked). With marks (default) the tracked objects are \
drawn as numbered boxes and the result maps each number to its track_id, so you can join what \
Glimmer says to the tables.
4. answer: the final reply in the owner's language, with the numbers, the time range they cover \
and one short sentence on how you got them and how sure you are.

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
            models = [SPARK_MODEL]
            for turn in range(MAX_STEPS + 1):
                last = turn == MAX_STEPS
                if last:
                    messages.append({"role": "user", "content": "Steps used up: answer now."})
                action = self._next(messages)
                if action.action == "answer" or last:
                    reply = action.answer or action.thought
                    steps.append(AskStep(action=action, result=None, seconds=0.0))
                    return AskAnswer(
                        question=question,
                        answer=reply,
                        steps=steps,
                        seconds=round(time.perf_counter() - started, 1),
                        models=models,
                    )
                t0 = time.perf_counter()
                result = self._run(action, tables, models)
                steps.append(
                    AskStep(
                        action=action, result=result, seconds=round(time.perf_counter() - t0, 1)
                    )
                )
                messages.append({"role": "assistant", "content": action.model_dump_json()})
                messages.append(
                    {
                        "role": "user",
                        "content": "Result:\n" + json.dumps(result, default=str)[:6000],
                    }
                )
        finally:
            tables.close()
        msg = "unreachable: the loop always answers on its last turn"
        raise AssertionError(msg)

    def _next(self, messages: list[dict[str, object]]) -> AskAction:
        """Spark's next action; a reply that is not a valid action ends with an apology."""
        try:
            return self._spark.structured(model=SPARK_MODEL, messages=messages, schema=AskAction)
        except RulecamError as exc:
            _log.warning("ask_spark_failed", error=str(exc)[:200])
            return AskAction(
                thought=f"Muse Spark did not return a valid step ({type(exc).__name__}).",
                action="answer",
                answer="Sorry, I could not finish working that out. Please ask again.",
            )

    def _opening(self, question: str, tables: TrackTables) -> list[dict[str, object]]:
        width, height = self._frame_size
        every = f"{self._caption_every_s:g} s" if self._caption_every_s else "(off: empty)"
        system = SYSTEM_PROMPT.format(
            width=width, height=height, timezone=self._timezone, caption_s=every
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

    def _run(self, action: AskAction, tables: TrackTables, models: list[str]) -> object:
        try:
            if action.action == "sql":
                return tables.query(action.sql or "").model_dump(mode="json")
            frames = self._frames.frames(action.camera_id or "", _instants(action.times))
            if not frames:
                return {"error": "no recorded frames at those instants on that camera"}
            if action.action == "count_objects":
                _note(models, "sam-3.1")
                counts = count_objects(self._segmenter, frames, action.concept or "person")
                return [c.model_dump(mode="json") for c in counts]
            _note(models, self._glimmer_model)
            labels: dict[str, str] = {}
            if action.marks and self._marks_dir is not None:
                frames = [self._mark(f, tables, labels) for f in frames]
            answer = look(self._glimmer, self._glimmer_model, frames, action.question or "")
            return {**answer.model_dump(), "marks": labels}
        except (SqlError, ValueError, RulecamError, OSError) as exc:
            _log.info("ask_step_failed", action=action.action, error=str(exc))
            return {"error": str(exc)}

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


def _note(models: list[str], model: str) -> None:
    if model not in models:
        models.append(model)


def _instants(times: Sequence[str]) -> list[datetime]:
    """Parse ISO-8601 instants (UTC when no offset is given); bad ones raise `ValueError`."""
    parsed = [datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times]
    return [t if t.tzinfo is not None else t.replace(tzinfo=UTC) for t in parsed]


def open_tables(
    root: Path,
    captions: Path | None = None,
    identities: Path | None = None,
    notes: Path | None = None,
) -> Callable[[], TrackTables]:
    """A factory of fresh snapshots of the track store at `root` and the memories' files."""
    return lambda: TrackTables(root, captions, identities, notes)


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
