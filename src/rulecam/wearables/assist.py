"""The glasses assistant: a point-of-view frame and maybe a question in, words for the ear out.

For each frame:

1. **Hear** (push-to-talk only): Muse Voice Transcribe turns the recorded question into text.
   Nothing is transcribed unless the wearer pressed to talk.
2. **Look**: Muse Glimmer looks at the frame with the question and the wearer's role, lists the
   hazards it sees and names up to three things SAM 3.1 should count. Meanwhile a question's
   frame is uploaded for Muse Spark and, in an aisle, `rulecam.shelf` finds the shelf gaps
   (Muse Spark proposes, SAM 3.1 confirms each is empty). When Glimmer does not answer in time,
   Muse Spark takes the look, and the answer says so.
3. **Count**: SAM 3.1 segments each thing Glimmer named, in parallel.
4. **Compose**: Muse Spark sees the frame and all of that evidence, checks it, and writes what to
   say in the wearer's ear.
5. **Speak**: the spoken line becomes a WAV with the repo's voice (Meta MMS-TTS, or the
   labelled system voice); without one the page speaks it with the browser's voice.

A patrol frame (no question) stops after the look when Glimmer sees nothing worth a warning, so
walking costs one Glimmer call per frame. An alert counts as **verified** only when Glimmer and
Spark both flagged it from the same frame; only verified patrol alerts are spoken.
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

import cv2

from rulecam.ask.vision import Frame, marked
from rulecam.ask.watch import FileAttacher
from rulecam.clients.open_weights.glimmer import user_message
from rulecam.contracts import TTS, Clock, Frozen, StructuredLLM, Transcriber
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter
from rulecam.shelf.gaps import ShelfReport, ShelfScanner
from rulecam.wearables.prompts import compose_prompt, look_prompt
from rulecam.wearables.schemas import (
    Composed,
    Count,
    Gap,
    GlimmerLook,
    Mode,
    PovAnswer,
    Stage,
)

_log = get_logger(__name__)

SPARK_MODEL: Final = "muse-spark-1.3"
SAM_MODEL: Final = "sam-3.1"
VOICE_MODEL: Final = "muse-voice-transcribe-1.0"
CAMERA_ID: Final = "glasses"
MAX_CONCEPTS: Final = 3
HISTORY: Final = 6  # earlier answers of the walk shown to Spark
DEFAULT_GLIMMER_TIMEOUT_S: Final = 25.0
GLIMMER_RETRY_S: Final = 120.0  # after a failed look, Spark looks at once for this long
PROBE_EVERY_S: Final = 60.0
GLIMMER_GRACE_S: Final = 6.0  # how long a racing Spark look waits for Glimmer's
SUFFIXES: Final = {"image/jpeg": ".jpg", "image/png": ".png"}
NOT_HEARD: Final = "Sorry, I didn't catch that. Hold the button and ask again."
GAPS_CAPTION: Final = "The same frame with the shelf gaps numbered:"
NOT_CHECKED: Final = "Sorry, I couldn't check that right now. Please try again."

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


class Timer:
    """Collects timed stages."""

    def __init__(self, monotonic: Callable[[], float]) -> None:
        """Create the timer with a monotonic clock."""
        self._monotonic = monotonic
        self._start = monotonic()
        self.stages: list[Stage] = []

    def now(self) -> float:
        """The monotonic time."""
        return self._monotonic()

    def add(self, name: str, model: str, since: float, *, ok: bool = True, note: str = "") -> None:
        """Record a stage that started at `since`."""
        ms = round((self._monotonic() - since) * 1000)
        self.stages.append(Stage(name=name, model=model, ms=ms, ok=ok, note=note[:300]))

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
    upload: asyncio.Task[tuple[Part | None, str]] | None = field(default=None)

    def uploaded(self, files: FileAttacher) -> asyncio.Task[tuple[Part | None, str]]:
        """The frame's upload for Spark, started on first need and shared after."""
        if self.upload is None:
            frame = self.frame
            self.upload = asyncio.create_task(timed(lambda: files.attach(frame)))
        return self.upload


@dataclass(frozen=True)
class _Findings:
    """What the models found for one frame."""

    look: GlimmerLook | None = None
    looked_by: str = ""
    counts: Sequence[Count] = ()
    gaps: Sequence[Gap] = ()
    composed: Composed | None = None
    marked: str | None = None


def concepts(look: GlimmerLook | None) -> list[str]:
    """The distinct, non-empty noun phrases Glimmer asked SAM 3.1 to count (at most 3)."""
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
        said = past.spoken or (past.look.scene if past.look else "")
        hazards = "; ".join(f"{h.what} ({h.where}, {h.severity})" for h in past.hazards)
        tail = f" Hazards: {hazards}" if hazards else ""
        lines.append(f"{past.at.isoformat()} [{past.kind}] {said}{tail}")
    return lines


def evidence_text(
    ask: Ask, found: _Findings, history: Sequence[PovAnswer], size: tuple[int, int]
) -> str:
    """Everything Spark gets besides the frame, as JSON."""
    look = found.look
    return json.dumps(
        {
            "question": ask.question or None,
            "frame_size_px": {"width": size[0], "height": size[1]},
            "first_look": {"by": found.looked_by, **look.model_dump()} if look else None,
            "sam_3_1_counts": [c.model_dump() for c in found.counts],
            "shelf_gaps_confirmed_by_sam": [g.model_dump() for g in found.gaps],
            "earlier_in_this_walk": history_lines(history),
        },
        ensure_ascii=False,
    )


def verifiers(found: _Findings, glimmer_model: str) -> list[str]:
    """The models that back an alert: Spark (composed it), Glimmer (flagged it), SAM (saw it)."""
    composed, look = found.composed, found.look
    if composed is None or not composed.alert:
        return []
    backing = [SPARK_MODEL]
    if look is not None and found.looked_by == glimmer_model and (look.alert or look.hazards):
        backing.append(glimmer_model)
    if any(c.count > 0 for c in found.counts):
        backing.append(SAM_MODEL)
    return backing


def fallback_spoken(look: GlimmerLook | None) -> str:
    """What to say when Spark could not compose: the first look's own answer, or an apology."""
    if look is not None and (look.answer or look.scene):
        return f"{look.answer or look.scene} (first look only, not checked)"
    return NOT_CHECKED


def image_size(frame: Path) -> tuple[int, int]:
    """The frame's width and height (0, 0 when it cannot be read)."""
    pixels = cv2.imread(str(frame))
    if pixels is None:
        return (0, 0)
    height, width = pixels.shape[:2]
    return (width, height)


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
        site_profile: str = "",
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Create the assistant.

        Args:
            spark: Muse Spark 1.3 (composes every answer; looks when Glimmer cannot).
            files: Uploads the frame for Spark (`SparkClient`).
            sam: SAM 3.1 on one image.
            out_dir: Where frames, marked frames and spoken WAVs are written.
            clock: Time source.
            glimmer: Muse Glimmer (the first look); `None` lets Spark look.
            glimmer_model: Glimmer's model name.
            shelf: The shelf-gap scanner used in aisle mode.
            transcriber: Muse Voice Transcribe for push-to-talk questions.
            voice: Speech for answers; `None` leaves speaking to the browser.
            glimmer_timeout_s: How long a look waits for Glimmer before Spark looks instead.
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
        self._site = site_profile
        self._monotonic = monotonic
        self._glimmer_down_until = 0.0
        self._glimmer_state: GlimmerState = "unknown"
        self._probed_at: float | None = None
        self._background: set[asyncio.Task[object]] = set()

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
        """Glimmer as last seen: `up`, `down` (Spark looks), `checking`, `unknown` or `none`."""
        return self._glimmer_state if self._glimmer is not None else "none"

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
        """Record whether Glimmer just answered; when down, Spark looks at once for a while."""
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
            The answer, with every stage timed.

        Raises:
            WearablesError: When the image is not a JPEG or PNG.
        """
        answer_id = f"{self._clock.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        run = _Run(ask, answer_id, self._save_frame(answer_id, ask), Timer(self._monotonic))
        if ask.audio is not None:
            run.kind, run.heard = "question", await self._hear(run)
            if not run.heard:
                return await self._finish(run, _Findings(), spoken=NOT_HEARD)
            run.ask = replace(ask, question=run.heard, audio=None)
        if run.ask.question:
            run.kind = "question"
            run.uploaded(self._files)
        shelf = asyncio.create_task(self._scan_shelf(run)) if self._wants_shelf(run) else None
        look, looked_by = await self._look(run)
        if run.kind == "patrol" and look is not None and not look.alert and not look.hazards:
            marked_name, gaps = await shelf if shelf is not None else (None, [])
            quiet = _Findings(look=look, looked_by=looked_by, gaps=gaps, marked=marked_name)
            return await self._finish(run, quiet)
        counts = await self._count(run, concepts(look))
        marked_name, gaps = await shelf if shelf is not None else (None, [])
        found = _Findings(
            look=look, looked_by=looked_by, counts=counts, gaps=gaps, marked=marked_name
        )
        composed = await self._compose(run, found, history)
        return await self._finish(
            run,
            replace(found, composed=composed, marked=marked_name or self._mark(run.frame, counts)),
        )

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
        since = run.timer.now()
        segments, error = await timed(
            lambda: transcriber.transcribe(audio, lang_bias=[lang] if lang else None)
        )
        text = " ".join(s.get("text", "").strip() for s in segments or []).strip()
        run.timer.add("hear", VOICE_MODEL, since, ok=not error, note=error or text)
        return text

    async def _look(self, run: _Run) -> tuple[GlimmerLook | None, str]:
        """Glimmer looks first; Spark looks too when Glimmer may be slow, or when it fails.

        Glimmer known up (or a patrol frame while it is not known down): wait for Glimmer, and
        let Spark look only if it fails. Otherwise (a question while Glimmer is cold or being
        checked, or any frame while it is down): both look at once, and Glimmer's look is used
        if it comes within `GLIMMER_GRACE_S` of Spark's. Glimmer always gets the frame, so a
        cold worker keeps warming up.
        """
        prompt = look_prompt(run.ask.mode, self._site)
        question = (
            f"The wearer asks: {run.ask.question}"
            if run.ask.question
            else "The wearer asked nothing; this is a patrol frame."
        )
        if self._glimmer is None:
            return await self._spark_look(run, prompt, question, "no Glimmer configured")
        glimmer = asyncio.create_task(self._glimmer_look(run, f"{prompt}\n{question}"))
        down = self._monotonic() < self._glimmer_down_until
        if not down and (self._glimmer_state == "up" or run.kind == "patrol"):
            look = await glimmer
            if look is not None:
                return look, self._glimmer_model
            reason = run.timer.stages[-1].note if run.timer.stages else ""
            return await self._spark_look(run, prompt, question, reason)
        state = "down lately" if down else f"not known to be up ({self._glimmer_state})"
        looked = await self._spark_look(run, prompt, question, state)
        if not glimmer.done():
            await asyncio.wait({glimmer}, timeout=GLIMMER_GRACE_S)
        if glimmer.done() and glimmer.result() is not None:
            return glimmer.result(), self._glimmer_model
        self._keep(glimmer)
        return looked

    async def _glimmer_look(self, run: _Run, text: str) -> GlimmerLook | None:
        """Glimmer's look, or `None`; a failure makes the next looks start Spark at once."""
        if self._glimmer is None:
            return None
        glimmer, frame, model = self._glimmer, run.frame, self._glimmer_model
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
        return look

    async def _spark_look(
        self, run: _Run, prompt: str, question: str, reason: str
    ) -> tuple[GlimmerLook | None, str]:
        part, error = await run.uploaded(self._files)
        since = run.timer.now()
        look = None
        if part is not None:
            content: list[Part] = [{"type": "input_text", "text": question}, part]
            look, error = await timed(
                lambda: self._spark.structured(
                    model=SPARK_MODEL,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": content},
                    ],
                    schema=GlimmerLook,
                )
            )
        note = f"Muse Spark looked: Glimmer {reason}"
        run.timer.add("look", SPARK_MODEL, since, ok=look is not None, note=f"{note} {error}")
        return look, (f"{SPARK_MODEL} (Glimmer unavailable)" if look is not None else "")

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

    async def _compose(
        self, run: _Run, found: _Findings, history: Sequence[PovAnswer]
    ) -> Composed | None:
        part, error = await run.uploaded(self._files)
        since = run.timer.now()
        if part is None:
            run.timer.add("compose", SPARK_MODEL, since, ok=False, note=f"upload failed: {error}")
            return None
        text = evidence_text(run.ask, found, history, image_size(run.frame))
        content: list[Part] = [{"type": "input_text", "text": text}, part]
        if found.gaps and found.marked:
            gaps_image = self.frames_dir / found.marked
            shown, _ = await timed(lambda: self._files.attach(gaps_image))
            if shown is not None:
                content += [{"type": "input_text", "text": GAPS_CAPTION}, shown]
        system = compose_prompt(run.ask.mode, self._site)
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
        run.timer.add("compose", SPARK_MODEL, since, ok=composed is not None, note=error)
        return composed

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

    async def _speak(self, run: _Run, text: str) -> str | None:
        if self._voice is None or not text or not run.ask.speak:
            return None
        tts, lang = self._voice.tts, run.ask.lang or "en"
        since = run.timer.now()
        wav, error = await timed(lambda: tts.synth(text, lang))
        run.timer.add("speak", self._voice.label, since, ok=wav is not None, note=error)
        if wav is None:
            return None
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        name = f"{run.answer_id}.wav"
        (self.audio_dir / name).write_bytes(wav)
        return name

    def _spoken(self, run: _Run, found: _Findings, verified: bool) -> str:
        """A question is always answered; a patrol frame speaks only a verified alert."""
        if found.composed is not None:
            return found.composed.spoken if run.kind == "question" or verified else ""
        return fallback_spoken(found.look) if run.kind == "question" else ""

    async def _finish(self, run: _Run, found: _Findings, *, spoken: str = "") -> PovAnswer:
        verified_by = verifiers(found, self._glimmer_model)
        verified = self._glimmer_model in verified_by
        spoken = spoken or self._spoken(run, found, verified)
        audio = await self._speak(run, spoken)
        composed, look = found.composed, found.look
        answer = PovAnswer(
            answer_id=run.answer_id,
            at=self._clock.now(),
            mode=run.ask.mode,
            kind=run.kind,
            question=run.ask.question,
            heard=run.heard,
            spoken=spoken,
            detail=composed.detail if composed is not None else (look.scene if look else ""),
            alert=composed.alert if composed is not None else False,
            verified=verified,
            verified_by=verified_by,
            severity=composed.severity if composed is not None else "none",
            confidence=composed.confidence if composed is not None else 0.0,
            hazards=list(composed.hazards) if composed is not None else [],
            counts=list(found.counts),
            gaps=list(found.gaps),
            look=look,
            looked_by=found.looked_by,
            frame=run.frame.name,
            marked=found.marked,
            audio=audio,
            voice=self._voice.label if audio and self._voice else None,
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
            ms=answer.total_ms,
        )
        return answer
