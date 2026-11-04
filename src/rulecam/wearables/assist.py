"""The glasses assistant: a point-of-view frame and maybe a question in, words for the ear out.

Built for a first spoken sentence in under ten seconds:

1. **At once, in parallel**: the frame is uploaded for Muse Spark while Muse Voice Transcribe
   hears the push-to-talk question (nothing is transcribed unless the wearer pressed to talk), and
   in an aisle `rulecam.shelf` looks for shelf gaps (Muse Spark proposes, SAM 3.1 confirms).
2. **Two looks, in parallel**: Muse Glimmer and Muse Spark each look at the frame with the
   question and answer in one short sentence. Whichever answers first is spoken straight away
   (the **first line**), with the voice kept warm; the page has already said "Looking."
3. **Count only when needed**: SAM 3.1 runs only when Spark says the answer needs an exact
   number. Then Spark checks the counts against the frame (with SAM's boxes drawn) and Glimmer's
   look, and rejects SAM labels that do not fit, e.g. a warning light boxed as a hard hat.
4. **The checked line**: an alert is **verified** only when Glimmer and Spark both flag it. The
   second line says the models agree, says what the second look saw when they disagree, or gives
   the checked numbers. A patrol frame (no question) is silent unless its alert is verified.

Every model text passes through `rulecam.wearables.privacy.scrub`: no clothing, hair, age or other
looks reach the ear or the screen, whatever the prompt managed.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Final, Literal

from rulecam.ask.vision import Frame, marked
from rulecam.ask.watch import FileAttacher
from rulecam.clients.open_weights.glimmer import user_message
from rulecam.contracts import TTS, Clock, Frozen, StructuredLLM, Transcriber
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter
from rulecam.shelf.gaps import ShelfReport, ShelfScanner
from rulecam.wearables.privacy import scrub, scrub_model
from rulecam.wearables.prompts import VOCABULARY, check_prompt, look_prompt, spark_look_prompt
from rulecam.wearables.schemas import (
    Composed,
    Count,
    Gap,
    GlimmerLook,
    Line,
    Mode,
    PovAnswer,
    SparkLook,
    Stage,
)

_log = get_logger(__name__)

SPARK_MODEL: Final = "muse-spark-1.3"
SAM_MODEL: Final = "sam-3.1"
VOICE_MODEL: Final = "muse-voice-transcribe-1.0"
CAMERA_ID: Final = "glasses"
MAX_CONCEPTS: Final = 3
HISTORY: Final = 4  # earlier answers of the walk shown to Spark
DEFAULT_GLIMMER_TIMEOUT_S: Final = 25.0
GLIMMER_RETRY_S: Final = 120.0  # after a failed look, Glimmer is not waited for this long
PROBE_EVERY_S: Final = 60.0
GLIMMER_GRACE_S: Final = 6.0  # how long the checked line waits for a Glimmer that is not up
SUFFIXES: Final = {"image/jpeg": ".jpg", "image/png": ".png"}
NOT_HEARD: Final = "Sorry, I didn't catch that. Hold the button and ask again."
NOT_CHECKED: Final = "Sorry, I couldn't check that right now. Please try again."
LOOKING: Final = "Looking."
AGREE: Final = "Both models agree."
SECOND: Final = "Second look: {line}"
PHRASES: Final = {"looking": LOOKING, "agree": AGREE, "not_heard": NOT_HEARD}
GAPS_CAPTION: Final = "The same frame with the shelf gaps numbered:"
BOXES_CAPTION: Final = "The frame with SAM 3.1's numbered boxes:"

Kind = Literal["question", "patrol"]
GlimmerState = Literal["unknown", "checking", "up", "down", "none"]
Part = dict[str, object]


class GlimmerPing(Frozen):
    """Glimmer's answer to a health check."""

    ok: bool


class WearablesError(ValueError):
    """The request cannot be answered as sent (bad image type)."""


@dataclass(frozen=True)
class Voice:
    """Speech for the wearer's ear and what to call it on screen."""

    tts: TTS
    label: str


@dataclass(frozen=True)
class Ask:
    """One frame from the glasses and what came with it."""

    image: bytes
    content_type: str = "image/jpeg"
    mode: Mode = "general"
    question: str | None = None
    audio: bytes | None = None
    lang: str = "en"
    speak: bool = True
    source: str = "phone-camera"
    walk_id: str | None = None
    progress: Callable[[Stage], None] | None = None  # told of each stage as it ends
    on_first: Callable[[Line], None] | None = None  # told of the first spoken line


class Timer:
    """Collects timed stages."""

    def __init__(
        self, monotonic: Callable[[], float], progress: Callable[[Stage], None] | None = None
    ) -> None:
        """Create the timer with a monotonic clock and an optional listener for each stage."""
        self._monotonic = monotonic
        self._progress = progress
        self._start = monotonic()
        self.stages: list[Stage] = []

    def now(self) -> float:
        """The monotonic time."""
        return self._monotonic()

    def add(self, name: str, model: str, since: float, *, ok: bool = True, note: str = "") -> None:
        """Record a stage that started at `since`."""
        ms = round((self._monotonic() - since) * 1000)
        stage = Stage(name=name, model=model, ms=ms, ok=ok, note=note[:300])
        self.stages.append(stage)
        if self._progress is not None:
            self._progress(stage)

    def total_ms(self) -> int:
        """Milliseconds since the timer was made."""
        return round((self._monotonic() - self._start) * 1000)


async def timed[R](call: Callable[[], R], timeout_s: float | None = None) -> tuple[R | None, str]:
    """Run a blocking call in a thread: `(result, '')`, or `(None, why it failed)`."""
    try:
        task = asyncio.to_thread(call)
        result = await (asyncio.wait_for(task, timeout_s) if timeout_s else task)
    except TimeoutError:
        return None, f"no answer within {timeout_s or 0:.0f} s"
    except Exception as exc:  # every model failure becomes a labelled stage
        return None, f"{type(exc).__name__}: {exc}"[:300]
    return result, ""


@dataclass
class _Run:
    """One answer in progress."""

    ask: Ask
    answer_id: str
    frame: Path
    timer: Timer
    kind: Kind = "patrol"
    heard: str | None = None
    privacy_edits: int = 0
    upload: asyncio.Task[tuple[Part | None, str]] | None = field(default=None)

    def uploaded(self, files: FileAttacher) -> asyncio.Task[tuple[Part | None, str]]:
        """The frame's upload for Spark, started on first need and shared after."""
        if self.upload is None:
            frame = self.frame
            self.upload = asyncio.create_task(timed(lambda: files.attach(frame)))
        return self.upload

    def clean[M: Frozen](self, model: M | None) -> M | None:
        """The model's text without appearance descriptions (edits counted)."""
        if model is None:
            return None
        cleaned, edits = scrub_model(model)
        self.privacy_edits += edits
        return cleaned


@dataclass(frozen=True)
class _Findings:
    """What the models found for one frame."""

    glimmer: GlimmerLook | None = None
    spark: SparkLook | None = None
    counts: Sequence[Count] = ()
    gaps: Sequence[Gap] = ()
    composed: Composed | None = None
    marked: str | None = None
    first: Line | None = None


def concepts(look: SparkLook | None) -> list[str]:
    """The distinct, non-empty noun phrases Spark asked SAM 3.1 to count (at most 3)."""
    seen: list[str] = []
    for raw in look.count if look is not None else []:
        phrase = " ".join(raw.split()).strip(" .").lower()[:60]
        if phrase and phrase not in seen:
            seen.append(phrase)
    return seen[:MAX_CONCEPTS]


def gaps_of(report: ShelfReport | None) -> list[Gap]:
    """The confirmed shelf gaps of a shelf report."""
    if report is None:
        return []
    return [
        Gap(number=g.number, section=g.section, neighbours=g.neighbours, facings=g.facings)
        for g in report.gaps
    ]


def history_lines(history: Sequence[PovAnswer]) -> list[str]:
    """Earlier answers of the walk as short lines for Spark."""
    lines = []
    for past in history[-HISTORY:]:
        said = (past.first.text if past.first else "") or past.spoken
        hazards = "; ".join(f"{h.what} ({h.where})" for h in past.hazards)
        tail = f" Hazards: {hazards}" if hazards else ""
        lines.append(f"[{past.kind}] {said}{tail}")
    return lines


def verdict(found: _Findings, glimmer_model: str) -> tuple[bool, list[str]]:
    """Whether the alert is verified (Glimmer and Spark both flag it) and who backs it."""
    spark_alert = (
        found.composed.alert if found.composed else bool(found.spark and found.spark.alert)
    )
    if not (spark_alert and found.glimmer is not None and found.glimmer.alert):
        return False, []
    backing = [SPARK_MODEL, glimmer_model]
    if any(c.count > 0 for c in found.counts):
        backing.append(SAM_MODEL)
    return True, backing


def second_line(found: _Findings, kind: Kind, verified: bool) -> str:
    """The checked line after the first, or '' when there is nothing to add."""
    spark, glimmer, composed = found.spark, found.glimmer, found.composed
    if kind == "patrol":
        said = composed.spoken if composed else (spark.spoken if spark else "")
        return said if verified else ""
    if composed is not None:
        return composed.spoken
    if found.first is None:
        return (spark.spoken if spark else "") or (glimmer.answer if glimmer else "") or NOT_CHECKED
    return _agreement(found.first, spark, glimmer, verified)


def _agreement(
    first: Line, spark: SparkLook | None, glimmer: GlimmerLook | None, verified: bool
) -> str:
    """After the first line: whether both models agree on an alert, or what the other saw."""
    if spark is None or glimmer is None or (not verified and spark.alert == glimmer.alert):
        return ""
    if verified:
        return AGREE
    other = glimmer.answer if first.by == SPARK_MODEL else spark.spoken
    return SECOND.format(line=other) if other else ""


def detail_of(found: _Findings) -> str:
    """The screen text: the check, or what each model said."""
    if found.composed is not None:
        return found.composed.detail
    parts = []
    if found.glimmer is not None:
        parts.append(f"Glimmer: {found.glimmer.scene} {found.glimmer.answer}".strip())
    if found.spark is not None and found.spark.spoken:
        parts.append(f"Spark: {found.spark.spoken}")
    return " | ".join(parts)


class PovAssistant:
    """Answers the wearer from their glasses' point of view."""

    def __init__(
        self,
        *,
        spark: StructuredLLM,
        files: FileAttacher,
        sam: ImageSegmenter,
        out_dir: Path,
        clock: Clock,
        glimmer: StructuredLLM | None = None,
        glimmer_model: str = "muse-glimmer-30b",
        shelf: ShelfScanner | None = None,
        transcriber: Transcriber | None = None,
        voice: Voice | None = None,
        glimmer_timeout_s: float = DEFAULT_GLIMMER_TIMEOUT_S,
        glimmer_grace_s: float = GLIMMER_GRACE_S,
        site_profile: str = "",
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Create the assistant.

        Args:
            spark: Muse Spark 1.3 (looks in parallel with Glimmer; checks counts and gaps).
            files: Uploads the frame for Spark (`SparkClient`).
            sam: SAM 3.1 on one image.
            out_dir: Where frames, marked frames and spoken WAVs are written.
            clock: Time source.
            glimmer: Muse Glimmer; `None` leaves every look to Spark (nothing is then verified).
            glimmer_model: Glimmer's model name.
            shelf: The shelf-gap scanner used in aisle mode.
            transcriber: Muse Voice Transcribe for push-to-talk questions.
            voice: Speech for answers; `None` leaves speaking to the browser.
            glimmer_timeout_s: How long a look waits for Glimmer at most.
            glimmer_grace_s: How long the checked line waits for a Glimmer not known to be up.
            site_profile: One line about the site for the prompts.
            monotonic: Timer (injected in tests).
        """
        self._spark = spark
        self._files = files
        self._sam = sam
        self._out = out_dir
        self._clock = clock
        self._glimmer = glimmer
        self._glimmer_model = glimmer_model
        self._shelf = shelf
        self._transcriber = transcriber
        self._voice = voice
        self._glimmer_timeout_s = glimmer_timeout_s
        self._glimmer_grace_s = glimmer_grace_s
        self._site = site_profile
        self._monotonic = monotonic
        self._glimmer_down_until = 0.0
        self._glimmer_state: GlimmerState = "unknown"
        self._probed_at: float | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self._phrases: dict[str, str] = {}  # phrase text -> WAV name, in English

    @property
    def frames_dir(self) -> Path:
        """Where frames and marked frames are written."""
        return self._out / "frames"

    @property
    def audio_dir(self) -> Path:
        """Where spoken answers are written."""
        return self._out / "audio"

    @property
    def voice(self) -> Voice | None:
        """The speech engine, if any."""
        return self._voice

    @property
    def glimmer_model(self) -> str | None:
        """Glimmer's model name, or `None` when Spark does every look."""
        return self._glimmer_model if self._glimmer is not None else None

    @property
    def can_hear(self) -> bool:
        """Whether push-to-talk questions can be transcribed."""
        return self._transcriber is not None

    @property
    def glimmer_state(self) -> GlimmerState:
        """Glimmer as last seen: `up`, `down`, `checking`, `unknown` or `none`."""
        return self._glimmer_state if self._glimmer is not None else "none"

    @property
    def phrases(self) -> dict[str, str]:
        """Fixed phrases already spoken into WAVs: key -> file name (e.g. `looking`)."""
        by_text = self._phrases
        return {key: by_text[text] for key, text in PHRASES.items() if text in by_text}

    def prepare_phrases(self) -> None:
        """Speak the fixed phrases once (this also loads the voice model, so it stays warm).

        Blocking: run it in a thread at startup.
        """
        if self._voice is None:
            return
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        for key, text in PHRASES.items():
            try:
                wav = self._voice.tts.synth(text, "en")
            except Exception as exc:  # a phrase that cannot be spoken is synthesised on demand
                _log.warning("wearables_phrase_failed", key=key, error=repr(exc)[:200])
                continue
            name = f"phrase-{key}.wav"
            (self.audio_dir / name).write_bytes(wav)
            self._phrases[text] = name
        _log.info("wearables_phrases_ready", phrases=sorted(self.phrases))

    def warm(self) -> GlimmerState:
        """Ping Glimmer in the background (at most once a minute) and return its last state.

        Called when the page opens, so a cold serverless worker starts before the first question
        and a Glimmer that is down is known before anyone waits for it.
        """
        now = self._monotonic()
        recent = self._probed_at is not None and now - self._probed_at < PROBE_EVERY_S
        if self._glimmer is None or recent or self._glimmer_state == "checking":
            return self.glimmer_state
        self._probed_at = now
        if self._glimmer_state != "up":  # an up Glimmer stays up while it is pinged again
            self._glimmer_state = "checking"
        self._keep(asyncio.create_task(self._probe()))
        return self._glimmer_state

    async def _probe(self) -> None:
        if self._glimmer is None:
            return
        glimmer, model = self._glimmer, self._glimmer_model
        reply, error = await timed(
            lambda: glimmer.structured(
                model=model,
                messages=[{"role": "user", "content": "Health check: reply with ok true."}],
                schema=GlimmerPing,
            ),
            self._glimmer_timeout_s,
        )
        self.mark_glimmer(up=reply is not None)
        _log.info("wearables_glimmer_probe", up=reply is not None, error=error)

    def mark_glimmer(self, *, up: bool) -> None:
        """Record whether Glimmer just answered; when down, the checked line does not wait."""
        self._glimmer_state = "up" if up else "down"
        self._glimmer_down_until = 0.0 if up else self._monotonic() + GLIMMER_RETRY_S

    def _keep(self, task: asyncio.Task[Any]) -> None:
        """Hold a background task until it ends."""
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def answer(self, ask: Ask, history: Sequence[PovAnswer] = ()) -> PovAnswer:
        """Answer one frame.

        Args:
            ask: The frame, the question or recorded audio, the mode and the walk.
            history: Earlier answers of the same walk, oldest first.

        Returns:
            The answer, with its first line, the checked line and every stage timed.

        Raises:
            WearablesError: When the image is not a JPEG or PNG.
        """
        answer_id = f"{self._clock.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        frame = self._save_frame(answer_id, ask)
        run = _Run(ask, answer_id, frame, Timer(self._monotonic, ask.progress))
        run.uploaded(self._files)  # in parallel with hearing the question
        if ask.audio is not None:
            run.kind, run.heard = "question", await self._hear(run)
            if not run.heard:
                return await self._finish(run, _Findings(), spoken=NOT_HEARD)
            run.ask = replace(ask, question=run.heard, audio=None)
        if run.ask.question:
            run.kind = "question"
        shelf = asyncio.create_task(self._scan_shelf(run)) if self._wants_shelf(run) else None
        glimmer = (
            asyncio.create_task(self._glimmer_look(run)) if self._glimmer is not None else None
        )
        spark = asyncio.create_task(self._spark_look(run, history))
        first = await self._first_line(run, glimmer, spark) if run.kind == "question" else None
        found = _Findings(
            spark=await spark, glimmer=await self._settle(glimmer, spark), first=first
        )
        counts = await self._count(run, concepts(found.spark))
        marked_name, gaps = await shelf if shelf is not None else (None, [])
        boxes = self._mark(run.frame, counts)
        found = replace(found, counts=counts, gaps=gaps, marked=marked_name or boxes)
        if counts or gaps:
            found = replace(found, composed=await self._check(run, found, boxes, history))
        return await self._finish(run, found)

    # --- stages -----------------------------------------------------------------------------

    def _save_frame(self, answer_id: str, ask: Ask) -> Path:
        suffix = SUFFIXES.get(ask.content_type)
        if suffix is None:
            msg = "send the frame as a JPEG or PNG"
            raise WearablesError(msg)
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        path = self.frames_dir / f"{answer_id}{suffix}"
        path.write_bytes(ask.image)
        return path

    def _wants_shelf(self, run: _Run) -> bool:
        """Aisle questions and aisle walk frames get a shelf-gap scan."""
        return (
            self._shelf is not None
            and run.ask.mode == "aisle"
            and (run.kind == "question" or run.ask.walk_id is not None)
        )

    async def _hear(self, run: _Run) -> str:
        if self._transcriber is None or run.ask.audio is None:
            return ""
        transcriber, audio, lang = self._transcriber, run.ask.audio, run.ask.lang
        mode = run.ask.mode
        since = run.timer.now()
        segments, error = await timed(
            lambda: transcriber.transcribe(
                audio, lang_bias=[lang] if lang else None, keywords=list(VOCABULARY[mode])
            )
        )
        text = " ".join(s.get("text", "").strip() for s in segments or []).strip()
        run.timer.add("hear", VOICE_MODEL, since, ok=not error, note=error or text)
        return text

    def _question_text(self, run: _Run) -> str:
        if run.ask.question:
            return f"The wearer asks: {run.ask.question}"
        return "The wearer asked nothing; this is a patrol frame."

    async def _glimmer_look(self, run: _Run) -> GlimmerLook | None:
        """Glimmer's look, or `None`; a failure marks Glimmer down for a while."""
        if self._glimmer is None:
            return None
        glimmer, frame, model = self._glimmer, run.frame, self._glimmer_model
        text = f"{look_prompt(run.ask.mode, self._site)}\n{self._question_text(run)}"
        since = run.timer.now()
        look, reason = await timed(
            lambda: glimmer.structured(
                model=model, messages=[user_message(text, [frame])], schema=GlimmerLook
            ),
            self._glimmer_timeout_s,
        )
        run.timer.add("look", model, since, ok=look is not None, note=reason)
        if look is None:
            _log.warning("wearables_glimmer_failed", error=reason)
        self.mark_glimmer(up=look is not None)
        return run.clean(look)

    async def _spark_look(self, run: _Run, history: Sequence[PovAnswer]) -> SparkLook | None:
        part, error = await run.uploaded(self._files)
        since = run.timer.now()
        look = None
        if part is not None:
            lines = history_lines(history)
            earlier = f" Earlier in this walk: {' / '.join(lines)}" if lines else ""
            content: list[Part] = [
                {"type": "input_text", "text": self._question_text(run) + earlier},
                part,
            ]
            system = spark_look_prompt(run.ask.mode, self._site)
            look, error = await timed(
                lambda: self._spark.structured(
                    model=SPARK_MODEL,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": content},
                    ],
                    schema=SparkLook,
                )
            )
        run.timer.add("look", SPARK_MODEL, since, ok=look is not None, note=error)
        return run.clean(look)

    async def _first_line(
        self,
        run: _Run,
        glimmer: asyncio.Task[GlimmerLook | None] | None,
        spark: asyncio.Task[SparkLook | None],
    ) -> Line | None:
        """Speak whichever look answers first."""
        pending: set[asyncio.Task[Any]] = {spark} if glimmer is None else {spark, glimmer}
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                look = task.result()
                if isinstance(look, GlimmerLook) and look.answer:
                    return await self._say_first(run, look.answer, self._glimmer_model)
                if isinstance(look, SparkLook) and look.spoken:
                    return await self._say_first(run, look.spoken, SPARK_MODEL)
        return None

    async def _say_first(self, run: _Run, text: str, by: str) -> Line:
        audio = await self._speak(run, text, "speak first")
        line = Line(text=text, audio=audio, by=by, at_ms=run.timer.total_ms())
        if run.ask.on_first is not None:
            run.ask.on_first(line)
        return line

    async def _settle(
        self,
        glimmer: asyncio.Task[GlimmerLook | None] | None,
        spark: asyncio.Task[SparkLook | None],
    ) -> GlimmerLook | None:
        """Glimmer's look for the checked line; not waited for long while Glimmer is not up."""
        if glimmer is None:
            return None
        if self._glimmer_state != "up" or self._monotonic() < self._glimmer_down_until:
            await spark
            if not glimmer.done():
                await asyncio.wait({glimmer}, timeout=self._glimmer_grace_s)
            if not glimmer.done():
                self._keep(glimmer)  # it still warms Glimmer up
                return None
        return await glimmer

    async def _scan_shelf(self, run: _Run) -> tuple[str | None, list[Gap]]:
        if self._shelf is None:
            return None, []
        shelf, frame = self._shelf, run.frame
        since = run.timer.now()
        report, error = await timed(lambda: shelf.scan_image(frame, camera_id=CAMERA_ID))
        gaps = gaps_of(report)
        rejected = len(report.rejected) if report is not None else 0
        note = error or f"{len(gaps)} gaps confirmed, {rejected} rejected by SAM 3.1"
        model = f"{SPARK_MODEL} + {SAM_MODEL}"
        run.timer.add("shelf", model, since, ok=report is not None, note=note)
        marked_image = report.marked_image if report is not None else None
        return (Path(marked_image).name if marked_image else None), gaps

    async def _count(self, run: _Run, phrases: Sequence[str]) -> list[Count]:
        if not phrases:
            return []
        sam, frame = self._sam, run.frame
        since = run.timer.now()

        def one(concept: str) -> Count:
            found = sam.segment_image(frame, concept, camera_id=CAMERA_ID)
            boxes = [
                [*(float(round(v)) for v in d.mask.box_xyxy), round(d.mask.confidence or 0.0, 3)]
                for d in found
            ]
            return Count(concept=concept, count=len(found), boxes=boxes)

        results = await asyncio.gather(*(timed(partial(one, c)) for c in phrases))
        counts = [r for r, _ in results if r is not None]
        errors = [e for _, e in results if e]
        note = ", ".join(f"{c.concept}: {c.count}" for c in counts)
        note += f" ({'; '.join(errors)})" if errors else ""
        run.timer.add("count", SAM_MODEL, since, ok=not errors, note=note)
        return counts

    async def _check(
        self, run: _Run, found: _Findings, boxes: str | None, history: Sequence[PovAnswer]
    ) -> Composed | None:
        """Spark checks SAM's counts (rejecting labels that do not fit) and the shelf gaps."""
        images: list[tuple[str | None, str | None]] = (
            [(BOXES_CAPTION, boxes)] if boxes else [(None, None)]
        )
        if found.gaps and found.marked and found.marked != boxes:
            images.append((GAPS_CAPTION, found.marked))
        content: list[Part] = [{"type": "input_text", "text": self._evidence(run, found, history)}]
        for caption, name in images:
            part, _ = await (
                timed(partial(self._files.attach, self.frames_dir / name))
                if name
                else run.uploaded(self._files)
            )
            if part is not None:
                if caption:
                    content.append({"type": "input_text", "text": caption})
                content.append(part)
        since = run.timer.now()
        system = check_prompt(run.ask.mode, self._site)
        composed, error = await timed(
            lambda: self._spark.structured(
                model=SPARK_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                schema=Composed,
            )
        )
        run.timer.add("check", SPARK_MODEL, since, ok=composed is not None, note=error)
        return run.clean(composed)

    def _evidence(self, run: _Run, found: _Findings, history: Sequence[PovAnswer]) -> str:
        return json.dumps(
            {
                "question": run.ask.question or None,
                "glimmer_look": found.glimmer.model_dump() if found.glimmer else None,
                "spark_first_look": found.spark.model_dump() if found.spark else None,
                "sam_3_1_counts": [
                    {"concept": c.concept, "count": c.count, "numbered_boxes": c.boxes[:12]}
                    for c in found.counts
                ],
                "shelf_gaps": [g.model_dump() for g in found.gaps],
                "earlier_in_this_walk": history_lines(history),
            },
            ensure_ascii=False,
        )

    def _mark(self, frame: Path, counts: Sequence[Count]) -> str | None:
        marks = [
            (str(k), c.concept, (b[0], b[1], b[2], b[3]))
            for c in counts
            for k, b in enumerate(c.boxes, 1)
        ]
        if not marks:
            return None
        shot = Frame(camera_id=CAMERA_ID, at=datetime.min, path=frame)
        return marked(shot, marks, self.frames_dir).path.name

    async def _speak(self, run: _Run, text: str, stage: str = "speak") -> str | None:
        if self._voice is None or not text or not run.ask.speak:
            return None
        lang = run.ask.lang or "en"
        ready = self._phrases.get(text) if lang == "en" else None
        if ready is not None:
            return ready  # spoken once at startup
        tts = self._voice.tts
        since = run.timer.now()
        wav, error = await timed(lambda: tts.synth(text, lang))
        run.timer.add(stage, self._voice.label, since, ok=wav is not None, note=error)
        if wav is None:
            return None
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        name = f"{run.answer_id}-{len(run.timer.stages)}.wav"
        (self.audio_dir / name).write_bytes(wav)
        return name

    async def _finish(self, run: _Run, found: _Findings, *, spoken: str = "") -> PovAnswer:
        verified, verified_by = verdict(found, self._glimmer_model)
        spoken = scrub(spoken or second_line(found, run.kind, verified))[0]
        audio = await self._speak(run, spoken)
        spark, composed = found.spark, found.composed
        source = composed or spark
        rejected = list(composed.rejected) if composed else []
        looked = [
            m for m, look in ((self._glimmer_model, found.glimmer), (SPARK_MODEL, spark)) if look
        ]
        answer = PovAnswer(
            answer_id=run.answer_id,
            at=self._clock.now(),
            mode=run.ask.mode,
            kind=run.kind,
            question=run.ask.question,
            heard=run.heard,
            first=found.first,
            spoken=spoken,
            detail=detail_of(found),
            alert=source.alert if source else False,
            verified=verified,
            verified_by=verified_by,
            severity=source.severity if source else "none",
            confidence=source.confidence if source else 0.0,
            hazards=list(source.hazards) if source else [],
            counts=list(found.counts),
            gaps=list(found.gaps),
            look=found.glimmer,
            spark_look=spark,
            rejected=rejected,
            privacy_edits=run.privacy_edits,
            looked_by=" + ".join(looked),
            frame=run.frame.name,
            marked=found.marked,
            audio=audio,
            voice=self._voice.label if (audio or found.first) and self._voice else None,
            stages=run.timer.stages,
            total_ms=run.timer.total_ms(),
            walk_id=run.ask.walk_id,
            source=run.ask.source,
        )
        _log.info(
            "wearables_answer",
            kind=run.kind,
            mode=run.ask.mode,
            alert=answer.alert,
            verified=verified,
            first_ms=found.first.at_ms if found.first else None,
            ms=answer.total_ms,
        )
        return answer
