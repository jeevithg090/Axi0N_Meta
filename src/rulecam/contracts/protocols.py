"""Interfaces for dependency injection (task book §4.7).

Feature code depends on these Protocols; real clients and test fakes both satisfy them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from rulecam.contracts.core import Chunk, Detection, Track
from rulecam.contracts.events import CandidateEvent, VerifierVerdict
from rulecam.contracts.rule_ir import RuleIR

M = TypeVar("M", bound=BaseModel)


class Clock(Protocol):
    """Source of the current time (inject `FakeClock` in tests)."""

    def now(self) -> datetime:
        """Return the current time as an aware datetime."""
        ...


class StructuredLLM(Protocol):
    """Responses-API style call returning a validated model (`tool_choice` is "auto" only)."""

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
    ) -> M:
        """Return the model's answer validated against `schema`."""
        ...


class Segmenter(Protocol):
    """SAM 3.1 video segmentation, one concept per call."""

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
        """Return detections for one concept in one chunk, sorted by frame index."""
        ...


class TrackStore(Protocol):
    """Persistent store of tracks."""

    def append(self, tracks: list[Track]) -> None:
        """Persist tracks."""
        ...

    def window(self, camera_id: str, t0: datetime, t1: datetime) -> list[Track]:
        """Return tracks for a camera with points inside `[t0, t1]`."""
        ...


class Verifier(Protocol):
    """Checks whether a candidate really violates its rule."""

    def verify(self, candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        """Return a verdict for one candidate."""
        ...


class TTS(Protocol):
    """Text to speech."""

    def synth(self, text: str, lang: str) -> bytes:
        """Return WAV bytes for `text` spoken in `lang`."""
        ...


class Transcriber(Protocol):
    """Speech to text."""

    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        """Return segments as `{speaker, text, lang}` dicts."""
        ...


class Notifier(Protocol):
    """Sends an alert to a role."""

    def send(
        self,
        role: str,
        title: str,
        body: str,
        *,
        clip_path: str | None = None,
        buttons: tuple[str, ...] = (),
    ) -> str:
        """Send an alert and return its message id."""
        ...


class Plug(Protocol):
    """A smart plug used for the interlock."""

    def off(self) -> None:
        """Cut power."""
        ...

    def on(self) -> None:
        """Restore power (human-approved resume only)."""
        ...
