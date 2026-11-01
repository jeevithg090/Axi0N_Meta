"""Muse Glimmer client (`GlimmerClient`): local open weights behind llama-server (task P3-03).

llama-server exposes an OpenAI-compatible chat API (`POST /v1/chat/completions`). Every call:

- asks for **JSON-schema-constrained output** through `response_format`, built from a pydantic
  model, and validates the reply against it; a malformed reply is retried once with a
  corrective message (so the retry is a different request with its own replay-cache key), then
  `ModelResponseError` is raised (callers decide the fallback);
- runs at temperature 0 with a low reasoning setting;
- goes through the replay cache via the injected `Transport`;
- publishes `relay.model_used` and appends one line to the open-weight evidence log
  (model, quant, hardware, latency, request hash; never image data). Replayed calls are not
  logged as evidence, because no inference ran.

Images are sent as base64 data URLs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from rulecam.bus import Bus
from rulecam.clients.errors import ModelHTTPError, ModelResponseError
from rulecam.clients.open_weights.evidence_log import EvidenceLog, InferenceLine
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import HttpxTransport, RetryPolicy, Sender, Transport
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)

PROVIDER = "glimmer"
CHAT_PATH = "/chat/completions"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_TOKENS = 1024
LOCAL_RPM = 600.0
JSON_ATTEMPTS = 2
SCHEMA_PROMPT = (
    "Reply with only a JSON object (no prose, no code fence) that matches this JSON Schema, "
    "with every required field present:\n{schema}"
)
RETRY_PROMPT = (
    "Your previous reply did not match the required JSON schema ({error}). "
    "Reply again with only a JSON object that matches the schema."
)
IMAGE_TYPES: Mapping[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def image_data_url(path: Path) -> str:
    """Return a file as a base64 `data:` URL.

    Args:
        path: A JPEG, PNG or WebP image.

    Returns:
        The data URL.

    Raises:
        ValueError: For an unsupported extension.
        OSError: If the file cannot be read.
    """
    content_type = IMAGE_TYPES.get(path.suffix.lower())
    if content_type is None:
        msg = f"unsupported image type {path.suffix!r}"
        raise ValueError(msg)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def user_message(text: str, images: Sequence[Path] = ()) -> dict[str, object]:
    """Build an OpenAI-style user message with text and optional images.

    Args:
        text: The prompt text.
        images: Image files to attach, in order.

    Returns:
        The message mapping.
    """
    parts: list[dict[str, object]] = [{"type": "text", "text": text}]
    parts.extend({"type": "image_url", "image_url": {"url": image_data_url(p)}} for p in images)
    return {"role": "user", "content": parts}


def request_hash(body: Mapping[str, object]) -> str:
    """Return the SHA-256 of a request body's canonical JSON (for the evidence log)."""
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_fence(content: str) -> str:
    """Drop a Markdown code fence some hosts wrap JSON replies in."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.removesuffix("```").strip()
    return text


def _message_content(body: object) -> str:
    try:
        content = body["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        msg = "Glimmer reply has no choices[0].message.content"
        raise ModelResponseError(msg) from exc
    if not isinstance(content, str):
        msg = "Glimmer reply content is not text"
        raise ModelResponseError(msg)
    return _strip_fence(content)


def _with_retry_hint(messages: list[dict[str, object]], error: str) -> list[dict[str, object]]:
    """Return `messages`, plus a corrective user turn when a previous reply was malformed."""
    if not error:
        return messages
    return [*messages, {"role": "user", "content": RETRY_PROMPT.format(error=error)}]


@dataclass(frozen=True)
class GlimmerConfig:
    """Static facts about the Glimmer deployment.

    Attributes:
        model: Model name sent in requests and shown on the relay HUD.
        quant: GGUF quantisation (for the evidence log), e.g. `k-quant-17G`.
        hardware: Where it runs (for the evidence log), e.g. `M3 Max 36 GB`.
        max_tokens: Output token cap per call.
        segment: Relay HUD segment name.
        schema_in_prompt: Also state the schema in a system message, for hosts that do not
            enforce `response_format` strictly.
    """

    model: str = "muse-glimmer-30b"
    quant: str = ""
    hardware: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS
    segment: str = "verify"
    schema_in_prompt: bool = False


class GlimmerClient:
    """Structured-output chat calls to the local Glimmer server."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock,
        config: GlimmerConfig | None = None,
        bus: Bus | None = None,
        evidence: EvidenceLog | None = None,
        monotonic: Callable[[], float] = time.perf_counter,
        replaying: Callable[[], bool] = lambda: False,
    ) -> None:
        """Create the client.

        Args:
            transport: The HTTP seam; normally `ReplayCache.wrap_transport(HttpxTransport(...))`.
            clock: Time source for evidence lines.
            config: Model name, quant, hardware and limits.
            bus: Bus for `relay.model_used`.
            evidence: Open-weight evidence log; `None` disables it (unit tests of other modules).
            monotonic: Timer for latency (injected in tests).
            replaying: Whether calls are currently served from the replay cache; replayed calls
                are not written to the evidence log.
        """
        self._config = config or GlimmerConfig()
        self._sender = Sender(transport, TokenBucket(LOCAL_RPM), RetryPolicy(max_attempts=2))
        self._clock = clock
        self._bus = bus
        self._evidence = evidence
        self._monotonic = monotonic
        self._replaying = replaying

    @classmethod
    def from_settings(
        cls, settings: Settings, *, cache: ReplayCache, clock: Clock, bus: Bus | None = None
    ) -> GlimmerClient:
        """Build a client for `GLIMMER_BASE_URL`, routed through the replay cache.

        Args:
            settings: Application settings.
            cache: The replay cache (record, replay or live).
            clock: Time source.
            bus: Bus for `relay.model_used`.

        Returns:
            The client.
        """
        config = GlimmerConfig(
            model=settings.glimmer_model,
            quant=settings.glimmer_quant,
            hardware=settings.glimmer_hardware,
            schema_in_prompt=settings.glimmer_schema_in_prompt,
        )
        # llama-server ignores the key unless started with --api-key (needed when it is remote).
        key = settings.glimmer_api_key.get_secret_value() if settings.glimmer_api_key else "local"
        http = HttpxTransport(settings.glimmer_base_url, key, timeout_s=DEFAULT_TIMEOUT_S)
        transport = cache.wrap_transport(PROVIDER, config.model, http)
        evidence = EvidenceLog(settings.evidence_dir / "glimmer" / "inference-log.jsonl")
        return cls(
            transport,
            clock=clock,
            config=config,
            bus=bus,
            evidence=evidence,
            replaying=lambda: cache.mode == "replay",
        )

    @property
    def model(self) -> str:
        """The model name used in requests."""
        return self._config.model

    def _body(
        self, messages: list[dict[str, object]], schema: type[BaseModel], effort: str
    ) -> dict[str, object]:
        if self._config.schema_in_prompt:
            schema_text = json.dumps(schema.model_json_schema(), separators=(",", ":"))
            hint: dict[str, object] = {
                "role": "system",
                "content": SCHEMA_PROMPT.format(schema=schema_text),
            }
            messages = [hint, *messages]
        return {
            "model": self._config.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self._config.max_tokens,
            "reasoning_effort": effort,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
        }

    def _send(self, body: dict[str, object], camera_id: str | None) -> str:
        started = self._monotonic()
        ok = False
        attempts = 1
        try:
            reply = self._sender.send({"method": "POST", "path": CHAT_PATH, "json": body})
            attempts = reply.attempts
            content = _message_content(reply.reply.body)
            ok = True
        except ModelHTTPError as exc:
            attempts = exc.attempts
            raise
        else:
            return content
        finally:
            latency_ms = max(0, round((self._monotonic() - started) * 1000))
            self._record(body, latency_ms, ok=ok, attempts=attempts, camera_id=camera_id)

    def _record(
        self,
        body: Mapping[str, object],
        latency_ms: int,
        *,
        ok: bool,
        attempts: int,
        camera_id: str | None,
    ) -> None:
        publish_model_used(
            self._bus,
            ModelUsed(
                model=self._config.model,
                segment=self._config.segment,
                latency_ms=latency_ms,
                camera_id=camera_id,
                attempts=attempts,
            ),
        )
        if self._evidence is not None and not self._replaying():
            self._evidence.append(
                InferenceLine(
                    ts=self._clock.now(),
                    model=self._config.model,
                    quant=self._config.quant,
                    hardware=self._config.hardware,
                    latency_ms=latency_ms,
                    request_hash=request_hash(body),
                    ok=ok,
                )
            )

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
        camera_id: str | None = None,
    ) -> M:
        """Ask Glimmer for JSON matching `schema` (implements `StructuredLLM`).

        Args:
            model: Ignored beyond logging; the deployment's model is used (one local model).
            messages: OpenAI-style chat messages.
            schema: The pydantic model the reply must validate against.
            reasoning_effort: Reasoning budget hint; interactive paths use `low`.
            background: Unsupported locally; accepted for protocol compatibility.
            camera_id: Camera the call is for, shown on the relay HUD.

        Returns:
            The validated reply.

        Raises:
            ModelResponseError: If both attempts return text that does not match the schema.
            ModelHTTPError: If the server keeps failing.
        """
        if model != self._config.model or background:
            _log.debug("glimmer_call_options_ignored", model=model, background=background)
        last_error = ""
        for attempt in range(1, JSON_ATTEMPTS + 1):
            body = self._body(_with_retry_hint(messages, last_error), schema, reasoning_effort)
            content = self._send(body, camera_id)
            try:
                return schema.model_validate_json(content)
            except ValidationError as exc:
                last_error = f"{exc.error_count()} validation errors"
                _log.warning("glimmer_malformed_json", attempt=attempt, error=last_error)
        msg = f"Glimmer returned malformed JSON twice ({last_error})"
        raise ModelResponseError(msg)
