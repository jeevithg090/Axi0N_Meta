"""Test doubles for every contract Protocol, plus small builders for contract objects.

Feature tests inject these instead of real clients so that no unit test touches the network
(rule R11). Each fake records its calls so tests can assert on them.
"""

from __future__ import annotations

import io
import wave
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, TypeVar

from pydantic import BaseModel

from rulecam.contracts import (
    CandidateEvent,
    Chunk,
    Detection,
    RuleIR,
    Track,
    TrackPoint,
    VerifierVerdict,
)

M = TypeVar("M", bound=BaseModel)

EPOCH = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


class FakeClock:
    """A controllable clock."""

    def __init__(self, start: datetime = EPOCH) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@dataclass
class SegmentCall:
    """One recorded call to `FakeSegmenter.segment`."""

    chunk_id: str
    concept: str
    box_prompts: dict[int, tuple[float, float, float, float]] | None
    negative_points: list[tuple[int, float, float]] | None
    direction: str
    include_confidence: bool


class FakeSegmenter:
    """Returns preset detections per concept (or from a callable) and records every call."""

    def __init__(
        self,
        by_concept: dict[str, list[Detection]] | None = None,
        responder: Callable[[Chunk, str], list[Detection]] | None = None,
    ) -> None:
        self._by_concept = by_concept or {}
        self._responder = responder
        self.calls: list[SegmentCall] = []

    def segment(
        self,
        chunk: Chunk,
        concept: str,
        *,
        box_prompts: dict[int, tuple[float, float, float, float]] | None = None,
        negative_points: list[tuple[int, float, float]] | None = None,
        direction: Literal["forward", "backward"] = "forward",
        include_confidence: bool = True,
    ) -> list[Detection]:
        self.calls.append(
            SegmentCall(
                chunk.chunk_id, concept, box_prompts, negative_points, direction, include_confidence
            )
        )
        if self._responder is not None:
            return self._responder(chunk, concept)
        return list(self._by_concept.get(concept, []))


class FakeLLM:
    """Returns queued responses, validated against the requested schema."""

    def __init__(self, responses: Iterable[BaseModel | dict[str, object]] = ()) -> None:
        self._responses: deque[BaseModel | dict[str, object]] = deque(responses)
        self.calls: list[dict[str, object]] = []

    def queue(self, response: BaseModel | dict[str, object]) -> None:
        self._responses.append(response)

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
    ) -> M:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "schema": schema.__name__,
                "reasoning_effort": reasoning_effort,
                "background": background,
            }
        )
        if not self._responses:
            msg = "FakeLLM has no queued response"
            raise AssertionError(msg)
        response = self._responses.popleft()
        data = response.model_dump() if isinstance(response, BaseModel) else response
        return schema.model_validate(data)


class FakeVerifier:
    """Returns a preset verdict (or one computed by a callable)."""

    def __init__(
        self,
        verdict: VerifierVerdict | None = None,
        responder: Callable[[CandidateEvent, RuleIR], VerifierVerdict] | None = None,
    ) -> None:
        self._verdict = verdict or make_verdict()
        self._responder = responder
        self.calls: list[tuple[CandidateEvent, RuleIR]] = []

    def verify(self, candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        self.calls.append((candidate, rule))
        if self._responder is not None:
            return self._responder(candidate, rule)
        return self._verdict


class FakeTTS:
    """Synthesises a short silent WAV whose length depends on the text, and records calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def synth(self, text: str, lang: str) -> bytes:
        self.calls.append((text, lang))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16_000)
            wav.writeframes(b"\x00\x00" * (160 * max(1, len(text))))
        return buffer.getvalue()


class FakeTranscriber:
    """Returns preset segments and records calls."""

    def __init__(self, segments: list[dict[str, str]] | None = None) -> None:
        self._segments = segments or []
        self.calls: list[dict[str, object]] = []

    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        self.calls.append(
            {
                "n_bytes": len(audio),
                "lang_bias": lang_bias,
                "keywords": keywords,
                "diarize": diarize,
            }
        )
        return list(self._segments)


@dataclass
class SentMessage:
    """One recorded notification."""

    role: str
    title: str
    body: str
    clip_path: str | None
    buttons: tuple[str, ...]


class FakeNotifier:
    """Records every notification and returns sequential ids."""

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []

    def send(
        self,
        role: str,
        title: str,
        body: str,
        *,
        clip_path: str | None = None,
        buttons: tuple[str, ...] = (),
    ) -> str:
        self.sent.append(SentMessage(role, title, body, clip_path, buttons))
        return f"msg-{len(self.sent)}"


@dataclass
class FakePlug:
    """Records power changes in order."""

    events: list[str] = field(default_factory=list)

    def off(self) -> None:
        self.events.append("off")

    def on(self) -> None:
        self.events.append("on")


class InMemoryTrackStore:
    """A `TrackStore` kept in memory."""

    def __init__(self) -> None:
        self._tracks: list[Track] = []

    def append(self, tracks: list[Track]) -> None:
        self._tracks.extend(tracks)

    def window(self, camera_id: str, t0: datetime, t1: datetime) -> list[Track]:
        out: list[Track] = []
        for track in self._tracks:
            if track.camera_id != camera_id:
                continue
            points = tuple(p for p in track.points if t0 <= p.ts <= t1)
            if points:
                out.append(track.model_copy(update={"points": points}))
        return out


# --- builders ---------------------------------------------------------------------------------


def make_chunk(
    camera_id: str = "cam1",
    index: int = 0,
    *,
    fps: float = 10.0,
    n_frames: int = 30,
    start: datetime = EPOCH,
) -> Chunk:
    """Build a `Chunk` with sensible defaults."""
    return Chunk(
        chunk_id=f"{camera_id}_{index:06d}",
        camera_id=camera_id,
        path=f"data/chunks/{camera_id}_{index:06d}.mp4",
        start_ts=start + timedelta(seconds=index * n_frames / fps),
        fps=fps,
        n_frames=n_frames,
        width=1280,
        height=720,
    )


def make_track(
    track_id: str = "cam1:person:0001",
    *,
    camera_id: str = "cam1",
    concept: str = "person",
    feet_m: Iterable[tuple[float, float]] = ((0.0, 0.0),),
    hz: float = 10.0,
    start: datetime = EPOCH,
) -> Track:
    """Build a `Track` that visits the given ground positions at `hz`."""
    points = []
    for i, (x, y) in enumerate(feet_m):
        px = (100.0 * x, 100.0 * y)
        points.append(
            TrackPoint(
                ts=start + timedelta(seconds=i / hz),
                frame_idx=i,
                box_xyxy=(px[0] - 20.0, px[1] - 80.0, px[0] + 20.0, px[1]),
                foot_px=px,
                foot_m=(x, y),
                confidence=0.9,
            )
        )
    return Track(track_id=track_id, camera_id=camera_id, concept=concept, points=tuple(points))


def make_verdict(
    *,
    tier: Literal["glimmer", "spark_open"] = "glimmer",
    verdict: Literal["violation", "no_violation", "unsure"] = "violation",
    score: float = 0.9,
) -> VerifierVerdict:
    """Build a `VerifierVerdict`."""
    return VerifierVerdict(
        tier=tier,
        verdict=verdict,
        score=score,
        reason="test",
        latency_ms=10,
        model_version="fake",
    )
