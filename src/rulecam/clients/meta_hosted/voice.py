"""Muse Voice Transcribe client (`VoiceTranscribeClient`) implementing `Transcriber` (task P2-01).

Features:
- File transcription endpoint (≤ 10 min, ≤ 32 MB; splits longer files into WAV chunks).
- Streaming session builder (`StreamingVoiceSession`) supporting DIARIZATION or ENDPOINTING with
  `keywords` and `languageBias` configured at session start only.
- Returns segments as `[{"speaker": speaker, "text": text, "lang": lang}]`.
- Replay-cached transport and telemetry publishing via `relay.model_used`.
"""

from __future__ import annotations

import base64
import io
import random
import time
import wave
from collections.abc import Callable
from typing import Literal

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError, ModelResponseError
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import HttpxTransport, RetryPolicy, Sender, Transport
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache, get_cache

_log = get_logger(__name__)

DEFAULT_MODEL = "muse-voice-transcribe"
SPEECH_TRANSCRIBE_PATH = "/speech/transcribe"
DEFAULT_RPM = 1000.0
DEFAULT_SEGMENT = "voice"
MAX_FILE_DURATION_S = 600.0
MAX_FILE_BYTES = 32 * 1024 * 1024


def split_wav_audio(
    audio_bytes: bytes,
    max_duration_s: float = MAX_FILE_DURATION_S,
    max_bytes: int = MAX_FILE_BYTES,
) -> list[bytes]:
    """Split audio into chunks of at most `max_duration_s` and `max_bytes`.

    If the audio is a valid WAV, chunks are split along frame boundaries and re-encoded
    with valid WAV headers. For non-WAV formats, byte-level splitting is applied.

    Args:
        audio_bytes: Raw audio data.
        max_duration_s: Maximum duration per chunk in seconds (default: 600s / 10 min).
        max_bytes: Maximum size per chunk in bytes (default: 32MB).

    Returns:
        List of audio chunks meeting the duration and size constraints.
    """
    if len(audio_bytes) == 0:
        return [b""]

    # Try parsing as WAV
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as r:
            params = r.getparams()
            nchannels = params.nchannels
            sampwidth = params.sampwidth
            framerate = params.framerate
            nframes = params.nframes

            bytes_per_frame = nchannels * sampwidth
            if bytes_per_frame == 0 or framerate == 0:
                return [audio_bytes]

            frames_per_duration = int(max_duration_s * framerate)
            frames_per_size = max(1, (max_bytes - 1024) // bytes_per_frame)
            chunk_frames_cap = min(frames_per_duration, frames_per_size)

            if nframes <= chunk_frames_cap and len(audio_bytes) <= max_bytes:
                return [audio_bytes]

            chunks: list[bytes] = []
            while True:
                frames = r.readframes(chunk_frames_cap)
                if not frames:
                    break
                out = io.BytesIO()
                with wave.open(out, "wb") as w:
                    w.setnchannels(nchannels)
                    w.setsampwidth(sampwidth)
                    w.setframerate(framerate)
                    w.writeframes(frames)
                chunks.append(out.getvalue())
            return chunks if chunks else [audio_bytes]
    except Exception:
        # Non-WAV or unparseable: fallback to byte chunks if over max_bytes
        if len(audio_bytes) > max_bytes:
            return [audio_bytes[i : i + max_bytes] for i in range(0, len(audio_bytes), max_bytes)]
        return [audio_bytes]


class StreamingVoiceSession:
    """A streaming Voice Transcribe session (DIARIZATION or ENDPOINTING).

    `keywords` and `languageBias` must be supplied at session start; any attempt to alter
    them after session initialisation raises ValueError.
    """

    def __init__(
        self,
        session_type: Literal["DIARIZATION", "ENDPOINTING"] = "DIARIZATION",
        *,
        keywords: list[str] | None = None,
        language_bias: list[str] | None = None,
        token: str | None = None,
    ) -> None:
        """Initialise the streaming session.

        Args:
            session_type: "DIARIZATION" or "ENDPOINTING".
            keywords: Keywords to bias recognition towards (set once at start).
            language_bias: Language tags to bias recognition towards (set once at start).
            token: Auth token for initial handshake.
        """
        self.session_type = session_type
        self._keywords = list(keywords) if keywords else None
        self._language_bias = list(language_bias) if language_bias else None
        self.token = token
        self._started = True

    @property
    def keywords(self) -> list[str] | None:
        """Keywords configured for this session."""
        return list(self._keywords) if self._keywords else None

    @property
    def language_bias(self) -> list[str] | None:
        """Language bias configured for this session."""
        return list(self._language_bias) if self._language_bias else None

    def set_keywords(self, _keywords: list[str]) -> None:
        """Disallowed after session start.

        Raises:
            ValueError: Always, because keywords must be provided at session creation.
        """
        msg = "keywords can only be set at session start"
        raise ValueError(msg)

    def set_language_bias(self, _language_bias: list[str]) -> None:
        """Disallowed after session start.

        Raises:
            ValueError: Always, because languageBias must be provided at session creation.
        """
        msg = "languageBias can only be set at session start"
        raise ValueError(msg)

    def initial_handshake(self) -> dict[str, object]:
        """Produce the first JSON handshake message for the streaming connection."""
        handshake: dict[str, object] = {
            "type": "start",
            "session_type": self.session_type,
        }
        if self.token:
            handshake["token"] = self.token
        if self._keywords:
            handshake["keywords"] = self._keywords
        if self._language_bias:
            handshake["languageBias"] = self._language_bias
        return handshake


def _parse_voice_segments(body: object, lang_bias: list[str] | None) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    default_lang = lang_bias[0] if lang_bias else "en"

    if isinstance(body, dict):
        if "segments" in body and isinstance(body["segments"], list):
            for item in body["segments"]:
                if isinstance(item, dict):
                    segments.append(
                        {
                            "speaker": str(item.get("speaker", "speaker_0")),
                            "text": str(item.get("text", "")),
                            "lang": str(item.get("lang", default_lang)),
                        }
                    )
        elif "text" in body and isinstance(body["text"], str):
            segments.append(
                {
                    "speaker": "speaker_0",
                    "text": body["text"],
                    "lang": default_lang,
                }
            )
    elif isinstance(body, list):
        for item in body:
            if isinstance(item, dict):
                segments.append(
                    {
                        "speaker": str(item.get("speaker", "speaker_0")),
                        "text": str(item.get("text", "")),
                        "lang": str(item.get("lang", default_lang)),
                    }
                )
    else:
        msg = f"Unexpected response from Voice Transcribe API: {body!r}"
        raise ModelResponseError(msg)
    return segments


class VoiceTranscribeClient:
    """Muse Voice Transcribe client on Meta's hosted API; satisfies `Transcriber`."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock,
        bus: Bus | None = None,
        cache: ReplayCache | None = None,
        model: str = DEFAULT_MODEL,
        rpm: float = DEFAULT_RPM,
        segment: str = DEFAULT_SEGMENT,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        seed: int | None = None,
    ) -> None:
        """Create the Voice Transcribe client.

        Args:
            transport: The HTTP transport callable.
            clock: Time source.
            bus: Event bus for `relay.model_used`.
            cache: Replay cache.
            model: Model name.
            rpm: Requests per minute rate limit.
            segment: Pipeline segment for telemetry.
            retry: Retry policy.
            sleep: Sleep callable.
            monotonic: Monotonic clock callable.
            seed: Seed for retry jitter.
        """
        self._clock = clock
        self._bus = bus
        self._cache = cache
        self._model = model
        self._segment = segment
        self._sleep = sleep
        self._monotonic = monotonic

        limiter = TokenBucket(rpm, monotonic=monotonic, sleep=sleep)
        jitter = random.Random(seed)  # noqa: S311 - retry jitter
        self._sender = Sender(transport, limiter, retry or RetryPolicy(), sleep, jitter)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Clock,
        bus: Bus | None = None,
        cache: ReplayCache | None = None,
    ) -> VoiceTranscribeClient:
        """Create client configured from application settings.

        Args:
            settings: Runtime settings.
            clock: Clock source.
            bus: Bus for relay events.
            cache: Replay cache.

        Returns:
            A configured VoiceTranscribeClient.

        Raises:
            ClientConfigError: When credentials or URLs are missing in live/record modes.
        """
        if settings.rulecam_mode == "replay" and cache is None:
            cache = get_cache()

        if settings.rulecam_mode != "replay" and (
            not settings.meta_api_base_url or settings.meta_api_key is None
        ):
            msg = "META_API_BASE_URL and META_API_KEY are required for hosted Voice Transcribe"
            raise ClientConfigError(msg)

        base_url = settings.meta_api_base_url or "https://api.meta.ai/v1"
        api_key = settings.meta_api_key.get_secret_value() if settings.meta_api_key else "replay"
        raw_transport = HttpxTransport(base_url, api_key)
        transport = (
            cache.wrap_transport("meta", DEFAULT_MODEL, raw_transport) if cache else raw_transport
        )
        return cls(transport, clock=clock, bus=bus, cache=cache)

    def start_streaming_session(
        self,
        session_type: Literal["DIARIZATION", "ENDPOINTING"] = "DIARIZATION",
        *,
        keywords: list[str] | None = None,
        language_bias: list[str] | None = None,
        token: str | None = None,
    ) -> StreamingVoiceSession:
        """Create a streaming session with keywords and languageBias set at start only."""
        return StreamingVoiceSession(
            session_type=session_type,
            keywords=keywords,
            language_bias=language_bias,
            token=token,
        )

    def _transcribe_single_chunk(
        self,
        chunk_bytes: bytes,
        *,
        lang_bias: list[str] | None,
        keywords: list[str] | None,
        diarize: bool,
    ) -> tuple[list[dict[str, str]], int, int]:
        started = self._monotonic()
        encoded = base64.b64encode(chunk_bytes).decode("ascii")
        req_json: dict[str, object] = {
            "model": self._model,
            "audio": encoded,
            "diarize": diarize,
        }
        if lang_bias:
            req_json["languageBias"] = lang_bias
        if keywords:
            req_json["keywords"] = keywords

        req: dict[str, object] = {
            "method": "POST",
            "path": SPEECH_TRANSCRIBE_PATH,
            "json": req_json,
        }
        reply = self._sender.send(req)
        latency_ms = max(0, round((self._monotonic() - started) * 1000))

        body = reply.reply.body
        segments = _parse_voice_segments(body, lang_bias)
        return segments, latency_ms, reply.attempts

    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        """Transcribe speech audio into diarized text segments.

        Automatically splits audio files exceeding 10 minutes or 32 MB before transcribing.

        Args:
            audio: Raw audio file bytes.
            lang_bias: Language codes to guide recognition (e.g. `["en", "es"]`).
            keywords: Domain vocabulary / terminology to bias recognition.
            diarize: Whether to identify and partition speakers.

        Returns:
            List of segments: `[{"speaker": speaker_id, "text": transcript, "lang": lang}]`.

        Raises:
            ModelHTTPError: If the transcription API encounters persistent HTTP failure.
            ModelResponseError: If the response cannot be decoded.
        """
        chunks = split_wav_audio(
            audio, max_duration_s=MAX_FILE_DURATION_S, max_bytes=MAX_FILE_BYTES
        )
        all_segments: list[dict[str, str]] = []
        total_latency_ms = 0
        total_attempts = 0

        for chunk in chunks:
            chunk_segments, latency_ms, attempts = self._transcribe_single_chunk(
                chunk,
                lang_bias=lang_bias,
                keywords=keywords,
                diarize=diarize,
            )
            all_segments.extend(chunk_segments)
            total_latency_ms += latency_ms
            total_attempts += attempts

        publish_model_used(
            self._bus,
            ModelUsed(
                model=self._model,
                segment=self._segment,
                latency_ms=total_latency_ms,
                attempts=max(1, total_attempts),
            ),
        )
        return all_segments
