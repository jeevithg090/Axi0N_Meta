"""Helpers for Voice Out tests: a recording bus, a stepping timer and WAV builders."""

from __future__ import annotations

import io
import wave

import numpy as np

from rulecam.bus import Bus, Message, Topic
from rulecam.voice_out.relay import ModelUsed

CASCO_ES = "¡Cuidado! Póngase el casco"


class RecordingBus(Bus):
    """A real bus that also keeps every published message for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[Message] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append(Message(topic=topic, payload=payload))
        super().publish(topic, payload)

    def models_used(self) -> list[ModelUsed]:
        return [
            m.payload
            for m in self.published
            if m.topic is Topic.RELAY_MODEL_USED and isinstance(m.payload, ModelUsed)
        ]


class StepTimer:
    """A monotonic timer that advances by `step` seconds on every reading."""

    def __init__(self, step: float = 0.25) -> None:
        self._now = 100.0
        self._step = step

    def __call__(self) -> float:
        self._now += self._step
        return self._now


def tone(seconds: float, sample_rate: int, *, amplitude: float = 0.5) -> np.ndarray:
    """A 440 Hz sine wave as float32 samples."""
    t = np.arange(round(seconds * sample_rate)) / sample_rate
    return (amplitude * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)


def pcm_wav(samples: np.ndarray, sample_rate: int, *, channels: int = 1, width: int = 2) -> bytes:
    """Encode float samples as a PCM WAV with the given layout (no resampling)."""
    scaled = np.round(np.clip(samples, -1, 1) * 32767).astype("<i2")
    interleaved = np.repeat(scaled, channels)
    frames = interleaved.tobytes() if width == 2 else bytes(len(interleaved))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)
    return buffer.getvalue()


def wav_info(data: bytes) -> tuple[int, int, int, int]:
    """Return `(channels, sample_width, sample_rate, n_frames)` of a WAV."""
    with wave.open(io.BytesIO(data), "rb") as wav:
        return wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
