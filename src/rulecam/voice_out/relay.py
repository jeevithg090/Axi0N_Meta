"""The `relay.model_used` payload Voice Out publishes after every synthesis (task book §4.8)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from pydantic import Field

from rulecam.bus import Bus, Topic
from rulecam.contracts import Frozen

Timer = Callable[[], float]
"""Monotonic seconds, e.g. `time.perf_counter`; injected so tests are deterministic."""

DEFAULT_SEGMENT: Final = "voice_out"


class ModelUsed(Frozen):
    """One engine run, shown on the relay HUD.

    Attributes:
        model: Model or engine id, e.g. `"facebook/mms-tts-spa"` or `"system-tts/say"`.
        segment: Pipeline segment the run belongs to.
        ms: Synthesis latency in milliseconds.
        meta: Whether the engine is a Meta model; the HUD greys out non-Meta engines.
    """

    model: str
    segment: str
    ms: int = Field(ge=0)
    meta: bool


def elapsed_ms(started: float, timer: Timer) -> int:
    """Return whole milliseconds since `started`, never negative.

    Args:
        started: A reading of `timer` taken before the work.
        timer: The same monotonic timer.

    Returns:
        Elapsed milliseconds.
    """
    return max(0, round(1000 * (timer() - started)))


def publish_model_used(bus: Bus, *, model: str, segment: str, ms: int, meta: bool) -> ModelUsed:
    """Publish a `ModelUsed` payload on `relay.model_used`.

    Args:
        bus: The event bus.
        model: Model or engine id.
        segment: Pipeline segment.
        ms: Latency in milliseconds.
        meta: Whether the engine is a Meta model.

    Returns:
        The published payload.
    """
    payload = ModelUsed(model=model, segment=segment, ms=ms, meta=meta)
    bus.publish(Topic.RELAY_MODEL_USED, payload)
    return payload
