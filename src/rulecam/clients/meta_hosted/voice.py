"""Muse Voice Transcribe client (`VoiceTranscribeClient`), the hosted `Transcriber` (task P2-01).

Voice Transcribe is the one hosted model that does not use `/responses`. Its wire format follows
the speech-to-text guide and the voice API reference (read Oct 31 2026,
<https://dev.meta.ai/docs/speech-to-text>, <https://dev.meta.ai/docs/api-reference/voice/schemas>):

- File transcription is `POST {base}/asr/transcribe`, `multipart/form-data` with a `request` part
  (JSON settings, `application/json`) and an `audio` part (RIFF/WAVE, mono, 16-bit PCM, 16 or
  24 kHz). At most 10 minutes and 32 MB per request, so longer WAVs are split on frame
  boundaries. Settings: `model`, `audioEncoding: "WAV"`, `mode` (`PUSH_TO_TALK`, `ENDPOINTING`
  or `DIARIZATION`), `languageBias` (language NAMES such as `"English"`) and `keywords`.
- The JSON reply is `{sessionId, transcript, audioDurationMs, turns: [{turnId, startMs, endMs,
  transcript, speaker?}]}`. `speaker` is present only in `DIARIZATION`; there is no language
  field, so a segment's `lang` is the bias language when exactly one was given, else `und`.
- Streaming is `wss://…/asr/realtime`. The key goes in the first JSON frame (the handshake), and
  `keywords` and `languageBias` can only be set there; `StreamingVoiceSession` builds it.

Every file request goes through the injected transport (`from_settings` always wraps it in the
replay cache; the audio bytes are keyed by their SHA-256) and publishes `relay.model_used`.
"""

from __future__ import annotations

import io
import json
import random
import time
import wave
from collections.abc import Callable, Mapping, Sequence
from typing import Literal

from rulecam.bus import Bus
from rulecam.clients.errors import ModelResponseError
from rulecam.clients.meta_hosted.responses import REPLAY_PROVIDER, latency_ms, live_transport
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import RetryPolicy, Sender, Transport
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

VOICE_MODEL = "muse-voice-transcribe-1.0"
TRANSCRIBE_PATH = "/asr/transcribe"
REALTIME_PATH = "/asr/realtime"
DEFAULT_RPM = 1000.0
DEFAULT_SEGMENT = "voice"
MAX_FILE_DURATION_S = 600.0
MAX_FILE_BYTES = 32 * 1024 * 1024
_MULTIPART_OVERHEAD_BYTES = 64 * 1024  # room for the `request` part and boundaries
_WAV_HEADER_BYTES = 44
PCM16_SAMPLE_BYTES = 2
SUPPORTED_SAMPLE_RATES = frozenset({16_000, 24_000})
UNKNOWN_LANG = "und"
DEFAULT_SPEAKER = "speaker_0"

Mode = Literal["PUSH_TO_TALK", "ENDPOINTING", "DIARIZATION"]
StreamMode = Literal["ENDPOINTING", "DIARIZATION"]

# BCP-47 primary subtags -> the language names `languageBias` takes. Names pass through as-is.
LANGUAGE_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "hi": "Hindi",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ru": "Russian",
    "th": "Thai",
    "tl": "Tagalog",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh": "Chinese",
}


def language_names(lang_bias: Sequence[str] | None) -> list[str]:
    """Map language tags (`en`, `es-MX`) to the names `languageBias` takes (`English`).

    Args:
        lang_bias: Language tags or names; unknown values are passed through unchanged.

    Returns:
        The names, de-duplicated in order.
    """
    names = (LANGUAGE_NAMES.get(tag.split("-", 1)[0].lower(), tag) for tag in lang_bias or ())
    return list(dict.fromkeys(names))


def check_wav(audio: bytes) -> None:
    """Check that `audio` is the WAV the API accepts: mono, 16-bit PCM, 16 or 24 kHz.

    Args:
        audio: The audio bytes.

    Raises:
        ValueError: If it is not such a WAV (convert it first, e.g. with ffmpeg).
    """
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            params = reader.getparams()
    except (wave.Error, EOFError) as exc:
        msg = "Voice Transcribe takes RIFF/WAVE audio; convert other formats first"
        raise ValueError(msg) from exc
    if (
        params.nchannels != 1
        or params.sampwidth != PCM16_SAMPLE_BYTES
        or params.framerate not in SUPPORTED_SAMPLE_RATES
    ):
        msg = (
            "Voice Transcribe takes mono 16-bit PCM at 16 or 24 kHz, got "
            f"{params.nchannels} ch, {8 * params.sampwidth}-bit, {params.framerate} Hz"
        )
        raise ValueError(msg)


def split_wav(
    audio: bytes, *, max_duration_s: float = MAX_FILE_DURATION_S, max_bytes: int = MAX_FILE_BYTES
) -> list[bytes]:
    """Split a WAV into valid WAVs of at most `max_duration_s` and `max_bytes` each.

    Args:
        audio: A WAV file.
        max_duration_s: Longest chunk in seconds (the file endpoint takes 10 minutes).
        max_bytes: Largest request body in bytes (32 MB), leaving room for the other parts.

    Returns:
        The chunks, in order; the input itself when it already fits.

    Raises:
        ValueError: If `audio` is not a WAV.
    """
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            params = reader.getparams()
            bytes_per_frame = params.nchannels * params.sampwidth
            budget = max_bytes - _MULTIPART_OVERHEAD_BYTES - _WAV_HEADER_BYTES
            cap = max(1, min(int(max_duration_s * params.framerate), budget // bytes_per_frame))
            if params.nframes <= cap:
                return [audio]
            chunks: list[bytes] = []
            while frames := reader.readframes(cap):
                out = io.BytesIO()
                with wave.open(out, "wb") as writer:
                    writer.setparams(params)
                    writer.writeframes(frames)
                chunks.append(out.getvalue())
            return chunks
    except (wave.Error, EOFError) as exc:
        msg = "Voice Transcribe takes RIFF/WAVE audio; convert other formats first"
        raise ValueError(msg) from exc


def transcribe_settings(
    *, model: str, mode: Mode, lang_bias: Sequence[str] | None, keywords: Sequence[str] | None
) -> dict[str, object]:
    """Build the JSON `request` part of a file transcription.

    Args:
        model: The model id.
        mode: Transcription mode.
        lang_bias: Language tags or names to bias toward; omitted to auto-detect.
        keywords: Vocabulary to bias toward.

    Returns:
        The settings object.
    """
    settings: dict[str, object] = {"model": model, "audioEncoding": "WAV", "mode": mode}
    names = language_names(lang_bias)
    if names:
        settings["languageBias"] = names
    if keywords:
        settings["keywords"] = list(keywords)
    return settings


def parse_transcript(body: object, *, lang: str) -> list[dict[str, str]]:
    """Turn a file transcription reply into `[{speaker, text, lang}]` segments.

    Args:
        body: The decoded JSON reply.
        lang: The `lang` to put on every segment (the API reports no language).

    Returns:
        One segment per turn; one segment for the whole transcript when there are no turns
        (`PUSH_TO_TALK`); `[]` for silence.

    Raises:
        ModelResponseError: If the reply does not have the documented shape.
    """
    if not isinstance(body, Mapping) or not isinstance(body.get("transcript"), str):
        msg = "Voice Transcribe reply has no transcript"
        raise ModelResponseError(msg)
    turns = body.get("turns")
    if not isinstance(turns, list):
        msg = "Voice Transcribe reply has no turns list"
        raise ModelResponseError(msg)
    segments = [
        {
            "speaker": str(turn.get("speaker") or DEFAULT_SPEAKER),
            "text": str(turn.get("transcript", "")).strip(),
            "lang": lang,
        }
        for turn in turns
        if isinstance(turn, Mapping)
    ]
    if not segments and str(body["transcript"]).strip():
        segments = [
            {"speaker": DEFAULT_SPEAKER, "text": str(body["transcript"]).strip(), "lang": lang}
        ]
    return [s for s in segments if s["text"]]


class StreamingVoiceSession:
    """The settings of one realtime session, fixed at start (`wss://…/asr/realtime`).

    `keywords` and `languageBias` are part of the first JSON frame, so they cannot change once the
    session has started: the setters always raise.
    """

    def __init__(
        self,
        mode: StreamMode = "DIARIZATION",
        *,
        api_key: str,
        model: str = VOICE_MODEL,
        keywords: Sequence[str] | None = None,
        lang_bias: Sequence[str] | None = None,
        audio_encoding: Literal["PCM_24KHZ", "PCM_16KHZ"] = "PCM_24KHZ",
    ) -> None:
        """Start the session's settings.

        Args:
            mode: `DIARIZATION` or `ENDPOINTING`.
            api_key: The bearer key; it goes in the handshake, never in a header or the log.
            model: The model id.
            keywords: Vocabulary bias, set now or never.
            lang_bias: Language tags or names, set now or never.
            audio_encoding: The PCM rate of the binary frames that follow.
        """
        self._mode: StreamMode = mode
        self._api_key = api_key
        self._model = model
        self._keywords = tuple(keywords or ())
        self._language_bias = tuple(language_names(lang_bias))
        self._audio_encoding = audio_encoding

    @property
    def keywords(self) -> tuple[str, ...]:
        """The keywords fixed at start."""
        return self._keywords

    @property
    def language_bias(self) -> tuple[str, ...]:
        """The `languageBias` names fixed at start."""
        return self._language_bias

    def set_keywords(self, keywords: Sequence[str]) -> None:
        """Refuse: keywords are fixed at session start.

        Args:
            keywords: Ignored.

        Raises:
            ValueError: Always.
        """
        msg = f"keywords can only be set at session start, not {list(keywords)!r}"
        raise ValueError(msg)

    def set_language_bias(self, lang_bias: Sequence[str]) -> None:
        """Refuse: `languageBias` is fixed at session start.

        Args:
            lang_bias: Ignored.

        Raises:
            ValueError: Always.
        """
        msg = f"languageBias can only be set at session start, not {list(lang_bias)!r}"
        raise ValueError(msg)

    def handshake(self) -> str:
        """Return the first JSON text frame to send within 10 s of connecting.

        Returns:
            The handshake, carrying the key, model, mode, encoding and biases.
        """
        frame: dict[str, object] = {
            "authorization": {"accessToken": f"Bearer {self._api_key}"},
            "audioEncoding": self._audio_encoding,
            "model": self._model,
            "mode": self._mode,
        }
        if self._language_bias:
            frame["languageBias"] = list(self._language_bias)
        if self._keywords:
            frame["keywords"] = list(self._keywords)
        return json.dumps(frame)


class VoiceTranscribeClient:
    """Muse Voice Transcribe on Meta's hosted API; satisfies `Transcriber`."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock,
        bus: Bus | None = None,
        model: str = VOICE_MODEL,
        rpm: float = DEFAULT_RPM,
        segment: str = DEFAULT_SEGMENT,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        seed: int | None = None,
    ) -> None:
        """Create the client.

        Args:
            transport: Sends one request mapping and returns the reply mapping.
            clock: Time source.
            bus: Bus for `relay.model_used`; `None` publishes nothing.
            model: The model id.
            rpm: Request budget per minute for this client.
            segment: Pipeline segment shown on the relay HUD.
            retry: Backoff policy for 429/5xx.
            sleep: Blocking sleep (injected in tests).
            monotonic: Monotonic time source (injected in tests).
            seed: Seed for the retry jitter.
        """
        self._clock = clock
        self._bus = bus
        self._model = model
        self._segment = segment
        self._monotonic = monotonic
        limiter = TokenBucket(rpm, monotonic=monotonic, sleep=sleep)
        jitter = random.Random(seed)  # noqa: S311 - retry jitter, not cryptography
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
        """Build a client whose transport always goes through the replay cache.

        Args:
            settings: Application settings.
            clock: Time source.
            bus: Bus for `relay.model_used` (and `mode.changed` of a cache built here).
            cache: The replay cache; by default one is built from `settings`.

        Returns:
            The client.

        Raises:
            ClientConfigError: In `live` or `record` mode without credentials.
        """
        if cache is None:
            cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
        inner = live_transport(settings, required=cache.mode != "replay", what="Voice Transcribe")
        transport = cache.wrap_transport(REPLAY_PROVIDER, VOICE_MODEL, inner)
        return cls(transport, clock=clock, bus=bus)

    def start_streaming_session(
        self,
        mode: StreamMode = "DIARIZATION",
        *,
        api_key: str,
        keywords: Sequence[str] | None = None,
        lang_bias: Sequence[str] | None = None,
    ) -> StreamingVoiceSession:
        """Fix a realtime session's settings; `keywords` and `languageBias` can't change later.

        Args:
            mode: `DIARIZATION` or `ENDPOINTING`.
            api_key: The bearer key for the handshake.
            keywords: Vocabulary bias.
            lang_bias: Language tags or names.

        Returns:
            The session, whose `handshake()` is the first frame to send.
        """
        return StreamingVoiceSession(
            mode, api_key=api_key, model=self._model, keywords=keywords, lang_bias=lang_bias
        )

    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        """Transcribe a WAV with the file endpoint, splitting it if it is too long.

        Args:
            audio: A mono 16-bit PCM WAV at 16 or 24 kHz.
            lang_bias: Language tags (`en`, `es`) or names to bias toward.
            keywords: Vocabulary to bias toward.
            diarize: Use `DIARIZATION` mode, so segments carry speaker labels.

        Returns:
            `[{"speaker", "text", "lang"}]` in order. Speaker labels of a split file are
            prefixed with the chunk index (`c1_…`), because each request labels speakers anew.

        Raises:
            ValueError: If the audio is not a supported WAV.
            ModelHTTPError: When the API keeps failing or rejects the request.
            ModelResponseError: When a reply does not have the documented shape.
        """
        check_wav(audio)
        chunks = split_wav(audio)
        settings = transcribe_settings(
            model=self._model,
            mode="DIARIZATION" if diarize else "PUSH_TO_TALK",
            lang_bias=lang_bias,
            keywords=keywords,
        )
        lang = lang_bias[0] if lang_bias and len(lang_bias) == 1 else UNKNOWN_LANG
        segments: list[dict[str, str]] = []
        total_ms = attempts = 0
        for index, chunk in enumerate(chunks):
            start = self._monotonic()
            result = self._sender.send(_transcribe_request(settings, chunk, index))
            total_ms += latency_ms(result, self._monotonic() - start)
            attempts += result.attempts
            for seg in parse_transcript(result.reply.body, lang=lang):
                if len(chunks) > 1 and diarize:
                    seg["speaker"] = f"c{index}_{seg['speaker']}"
                segments.append(seg)
        publish_model_used(
            self._bus,
            ModelUsed(
                model=self._model, segment=self._segment, latency_ms=total_ms, attempts=attempts
            ),
        )
        _log.info("voice_transcribe", chunks=len(chunks), segments=len(segments), diarize=diarize)
        return segments


def _transcribe_request(
    settings: Mapping[str, object], chunk: bytes, index: int
) -> dict[str, object]:
    return {
        "method": "POST",
        "path": TRANSCRIBE_PATH,
        "parts": {
            "request": {
                "content_type": "application/json",
                "content": json.dumps(settings, sort_keys=True).encode("utf-8"),
            },
            "audio": {
                "filename": f"audio_{index}.wav",
                "content_type": "audio/wav",
                "content": chunk,
            },
        },
    }
