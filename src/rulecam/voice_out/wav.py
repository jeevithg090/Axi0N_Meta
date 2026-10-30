"""WAV encoding for Voice Out: every phrase leaves this package as 16 kHz mono 16-bit PCM."""

from __future__ import annotations

import io
import math
import wave
from typing import Final

import numpy as np
import numpy.typing as npt
from scipy.signal import resample_poly  # type: ignore[import-untyped]

from rulecam.voice_out.errors import SynthesisError

TARGET_SAMPLE_RATE: Final = 16_000
_SAMPLE_WIDTH_BYTES: Final = 2
_INT16_SCALE: Final = 32_767.0

Samples = npt.NDArray[np.float32]


def resample(samples: Samples, sample_rate: int, target_rate: int = TARGET_SAMPLE_RATE) -> Samples:
    """Resample a mono signal with a polyphase filter.

    Args:
        samples: Mono samples in `[-1, 1]`.
        sample_rate: Rate of `samples` in Hz.
        target_rate: Wanted rate in Hz.

    Returns:
        The resampled signal (the input itself when the rates already match).
    """
    if sample_rate == target_rate:
        return samples
    divisor = math.gcd(sample_rate, target_rate)
    resampled = resample_poly(samples, target_rate // divisor, sample_rate // divisor)
    return np.asarray(resampled, dtype=np.float32)


def encode_wav(samples: npt.ArrayLike, sample_rate: int) -> bytes:
    """Encode a float waveform as a 16 kHz mono 16-bit PCM WAV.

    Args:
        samples: Mono samples in `[-1, 1]`; any shape that flattens to one channel (MMS returns
            `(1, n)`). Values outside the range are clipped.
        sample_rate: Rate of `samples` in Hz; resampled to 16 kHz if different.

    Returns:
        The WAV file as bytes.
    """
    mono = np.asarray(samples, dtype=np.float32).reshape(-1)
    clipped = np.clip(resample(mono, sample_rate), -1.0, 1.0)
    pcm = np.round(clipped * _INT16_SCALE).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(_SAMPLE_WIDTH_BYTES)
        wav.setframerate(TARGET_SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def decode_wav(data: bytes) -> tuple[Samples, int]:
    """Decode a 16-bit PCM WAV into mono float samples.

    Args:
        data: The WAV file as bytes; stereo is mixed down to mono.

    Returns:
        `(samples, sample_rate)` with samples in `[-1, 1]`.

    Raises:
        SynthesisError: If the bytes are not a 16-bit PCM WAV.
    """
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        msg = "audio is not a readable WAV file"
        raise SynthesisError(msg) from exc
    if width != _SAMPLE_WIDTH_BYTES:
        msg = f"expected 16-bit PCM, got {8 * width}-bit"
        raise SynthesisError(msg)
    pcm = np.frombuffer(frames, dtype="<i2").reshape(-1, channels)
    return (pcm.mean(axis=1) / (_INT16_SCALE + 1)).astype(np.float32), rate


def to_16k_wav(data: bytes) -> bytes:
    """Re-encode any 16-bit PCM WAV as 16 kHz mono.

    Args:
        data: The source WAV file as bytes.

    Returns:
        The 16 kHz mono WAV as bytes.

    Raises:
        SynthesisError: If `data` is not a 16-bit PCM WAV.
    """
    samples, rate = decode_wav(data)
    return encode_wav(samples, rate)
