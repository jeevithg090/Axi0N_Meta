"""Muse Glimmer client (`GlimmerClient`): local open weights behind llama-server (task P3-03).

llama-server exposes an OpenAI-compatible chat API (`POST /v1/chat/completions`). Every call:

- asks for **JSON-schema-constrained output** through `response_format`, built from a pydantic
  model, and validates the reply against it; a malformed reply is retried once with a
  corrective message (so the retry is a different request with its own replay-cache key), then
  `ModelResponseError` is raised (callers decide the fallback);
- runs at temperature 0 with a low reasoning setting and, when configured, llama.cpp's
  per-request `reasoning_budget_tokens` (the thinking tokens allowed before the answer);
- checks `finish_reason`: a reply cut at `max_tokens` (`length`) is not malformed JSON, so it is
  retried with the cap doubled (up to `max_tokens_ceiling`), then `ModelTruncatedError` is
  raised;
- keeps the model's `reasoning_content` in the `ReasoningStore` (it explains a rejection) and
  logs the reply's `usage` and llama.cpp `timings`;
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
from rulecam.clients.errors import ModelHTTPError, ModelResponseError, ModelTruncatedError
from rulecam.clients.open_weights.evidence_log import EvidenceLog, InferenceLine
from rulecam.clients.open_weights.llama_edge import KEY_HEADER
from rulecam.clients.open_weights.reasoning import ReasoningStore, reasoning_id
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import (
    HttpxTransport,
    PooledTransport,
    RetryPolicy,
    Sender,
    Transport,
)
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
DEFAULT_MAX_TOKENS_CEILING = 4096
NO_BUDGET = -1
CUT = "length"
BUDGET_MESSAGE = "\n(Thinking budget reached: give the JSON answer now.)\n"
USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")
TIMING_KEYS = ("prompt_ms", "predicted_ms", "predicted_per_second", "predicted_n", "prompt_n")
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


def glimmer_extra_urls(settings: Settings) -> list[str]:
    """The extra Glimmer servers of `GLIMMER_EXTRA_BASE_URLS`, in order, blanks dropped."""
    return [url.strip() for url in settings.glimmer_extra_base_urls.split(",") if url.strip()]


def glimmer_headers(settings: Settings) -> dict[str, str]:
    """Headers that authenticate to llama-server, directly or behind a RunPod load balancer.

    Args:
        settings: Application settings.

    Returns:
        `Authorization: Bearer GLIMMER_API_KEY`, or with `GLIMMER_EDGE_KEY` set that RunPod key
        as the bearer and the Glimmer key in `X-Rulecam-Key`; empty without a key.
    """
    if settings.glimmer_api_key is None:
        return {}
    key = settings.glimmer_api_key.get_secret_value()
    if settings.glimmer_edge_key is None:
        return {"Authorization": f"Bearer {key}"}
    edge = settings.glimmer_edge_key.get_secret_value()
    return {"Authorization": f"Bearer {edge}", KEY_HEADER: key}


def glimmer_transport(url: str, settings: Settings) -> HttpxTransport:
    """`HttpxTransport` to one llama-server (see `glimmer_headers`)."""
    # llama-server ignores the key unless started with --api-key (needed when it is remote).
    headers = glimmer_headers(settings)
    bearer = headers.pop("Authorization", "Bearer local").removeprefix("Bearer ")
    return HttpxTransport(url, bearer, timeout_s=DEFAULT_TIMEOUT_S, headers=headers)


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


@dataclass(frozen=True)
class Completion:
    """What one chat completion said.

    Attributes:
        content: The answer text (code fence removed; empty when cut before it began).
        reasoning: `message.reasoning_content` (empty when the server sent none).
        finish_reason: `stop`, `length` (cut at `max_tokens`), or empty when not reported.
        usage: Token counts and llama.cpp timings, flattened (see `usage_of`).
    """

    content: str
    reasoning: str
    finish_reason: str
    usage: dict[str, float]


def _numbers(block: object, keys: Sequence[str]) -> dict[str, float]:
    if not isinstance(block, Mapping):
        return {}
    return {
        k: float(v)
        for k in keys
        if isinstance(v := block.get(k), int | float) and not isinstance(v, bool)
    }


def usage_of(body: Mapping[str, object]) -> dict[str, float]:
    """Token counts (`usage`, with `reasoning_tokens` when given) and llama.cpp's `timings`."""
    usage = body.get("usage")
    found = _numbers(usage, USAGE_KEYS)
    details = usage.get("completion_tokens_details") if isinstance(usage, Mapping) else None
    found.update(_numbers(details, ("reasoning_tokens",)))
    found.update(_numbers(body.get("timings"), TIMING_KEYS))
    return found


def parse_completion(body: object) -> Completion:
    """Read a chat-completion body.

    Raises:
        ModelResponseError: If it has no first choice, or its content is not text (null
            content is read as empty only when the reply was cut).
    """
    try:
        choice = body["choices"][0]  # type: ignore[index]
        message = choice["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as exc:
        msg = "Glimmer reply has no choices[0].message.content"
        raise ModelResponseError(msg) from exc
    finish = str(choice.get("finish_reason") or "")
    if content is None and finish == CUT:
        content = ""
    if not isinstance(content, str):
        msg = "Glimmer reply content is not text"
        raise ModelResponseError(msg)
    reasoning = message.get("reasoning_content")
    return Completion(
        content=_strip_fence(content),
        reasoning=reasoning if isinstance(reasoning, str) else "",
        finish_reason=finish,
        usage=usage_of(body) if isinstance(body, Mapping) else {},
    )


@dataclass(frozen=True)
class GlimmerReply[A: BaseModel]:
    """A validated answer with how Glimmer got there.

    Attributes:
        answer: The reply, validated against the schema.
        reasoning_ref: Id of the stored reasoning, or `None` when there was none (or no store).
        finish_reason: The accepted reply's `finish_reason`.
        usage: The accepted reply's token counts and timings.
        max_tokens: The cap the accepted reply was given.
        calls: Chat calls made (cut and malformed replies included).
        latency_ms: Wall-clock time over all calls.
    """

    answer: A
    reasoning_ref: str | None
    finish_reason: str
    usage: dict[str, float]
    max_tokens: int
    calls: int
    latency_ms: int


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
        max_tokens: Output token cap per call, reasoning included.
        max_tokens_ceiling: Highest cap a cut reply (`finish_reason: length`) is retried with.
        reasoning_budget_tokens: llama.cpp's per-request thinking budget; -1 sends none (the
            server's `--reasoning-budget` applies), 0 turns thinking off.
        segment: Relay HUD segment name.
        schema_in_prompt: Also state the schema in a system message, for hosts that do not
            enforce `response_format` strictly.
    """

    model: str = "muse-glimmer-30b"
    quant: str = ""
    hardware: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_tokens_ceiling: int = DEFAULT_MAX_TOKENS_CEILING
    reasoning_budget_tokens: int = NO_BUDGET
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
        reasoning: ReasoningStore | None = None,
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
            reasoning: Where Glimmer's `reasoning_content` is kept; `None` drops it.
        """
        self._config = config or GlimmerConfig()
        self._sender = Sender(transport, TokenBucket(LOCAL_RPM), RetryPolicy(max_attempts=2))
        self._clock = clock
        self._bus = bus
        self._evidence = evidence
        self._monotonic = monotonic
        self._replaying = replaying
        self._reasoning = reasoning

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
            max_tokens=settings.glimmer_max_tokens,
            max_tokens_ceiling=settings.glimmer_max_tokens_ceiling,
            reasoning_budget_tokens=settings.glimmer_reasoning_budget_tokens,
        )
        urls = [settings.glimmer_base_url, *glimmer_extra_urls(settings)]
        servers = [glimmer_transport(url, settings) for url in urls]
        http: Transport = servers[0] if len(servers) == 1 else PooledTransport(servers)
        transport = cache.wrap_transport(PROVIDER, config.model, http)
        evidence = EvidenceLog(settings.evidence_dir / "glimmer" / "inference-log.jsonl")
        return cls(
            transport,
            clock=clock,
            config=config,
            bus=bus,
            evidence=evidence,
            replaying=lambda: cache.mode == "replay",
            reasoning=ReasoningStore(settings.rulecam_data_dir / "reasoning"),
        )

    @property
    def model(self) -> str:
        """The model name used in requests."""
        return self._config.model

    def _body(
        self,
        messages: list[dict[str, object]],
        schema: type[BaseModel],
        effort: str,
        max_tokens: int,
    ) -> dict[str, object]:
        if self._config.schema_in_prompt:
            schema_text = json.dumps(schema.model_json_schema(), separators=(",", ":"))
            hint: dict[str, object] = {
                "role": "system",
                "content": SCHEMA_PROMPT.format(schema=schema_text),
            }
            messages = [hint, *messages]
        body: dict[str, object] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
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
        budget = self._config.reasoning_budget_tokens
        if budget > NO_BUDGET:
            body["reasoning_budget_tokens"] = budget
            body["reasoning_budget_message"] = BUDGET_MESSAGE
        return body

    def _send(self, body: dict[str, object], camera_id: str | None) -> Completion:
        started = self._monotonic()
        attempts = 1
        done: Completion | None = None
        reasoning_ref: str | None = None
        try:
            reply = self._sender.send({"method": "POST", "path": CHAT_PATH, "json": body})
            attempts = reply.attempts
            done = parse_completion(reply.reply.body)
            if done.reasoning and self._reasoning is not None:
                reasoning_ref = self._reasoning.save(done.reasoning)
        except ModelHTTPError as exc:
            attempts = exc.attempts
            raise
        else:
            _log.info(
                "glimmer_usage",
                finish_reason=done.finish_reason,
                max_tokens=body["max_tokens"],
                reasoning_budget_tokens=body.get("reasoning_budget_tokens"),
                reasoning_ref=reasoning_ref,
                **done.usage,
            )
            return done
        finally:
            latency_ms = max(0, round((self._monotonic() - started) * 1000))
            self._record(
                body,
                latency_ms,
                attempts=attempts,
                camera_id=camera_id,
                done=done,
                reasoning_ref=reasoning_ref,
            )

    def _record(
        self,
        body: Mapping[str, object],
        latency_ms: int,
        *,
        attempts: int,
        camera_id: str | None,
        done: Completion | None,
        reasoning_ref: str | None,
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
                    ok=done is not None,
                    finish_reason=(done.finish_reason or None) if done else None,
                    usage=done.usage if done else {},
                    reasoning_ref=reasoning_ref,
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
            reasoning_effort: Reasoning hint for the chat template; interactive paths use `low`.
            background: Unsupported locally; accepted for protocol compatibility.
            camera_id: Camera the call is for, shown on the relay HUD.

        Returns:
            The validated reply.

        Raises:
            ModelTruncatedError: If the reply is still cut at `max_tokens_ceiling`.
            ModelResponseError: If two replies do not match the schema.
            ModelHTTPError: If the server keeps failing.
        """
        if model != self._config.model or background:
            _log.debug("glimmer_call_options_ignored", model=model, background=background)
        return self.structured_reply(
            messages, schema, reasoning_effort=reasoning_effort, camera_id=camera_id
        ).answer

    def structured_reply(
        self,
        messages: list[dict[str, object]],
        schema: type[M],
        *,
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        camera_id: str | None = None,
    ) -> GlimmerReply[M]:
        """Like `structured`, with the reasoning reference, usage and cap of the accepted reply.

        A reply that does not validate is retried: with the cap doubled when it was cut
        (`finish_reason: length`), else once with a corrective message.

        Raises:
            ModelTruncatedError: If the reply is still cut at `max_tokens_ceiling`.
            ModelResponseError: If two replies do not match the schema.
            ModelHTTPError: If the server keeps failing.
        """
        started = self._monotonic()
        cap, calls, malformed, last_error = self._config.max_tokens, 0, 0, ""
        while True:
            body = self._body(_with_retry_hint(messages, last_error), schema, reasoning_effort, cap)
            done = self._send(body, camera_id)
            calls += 1
            try:
                answer = schema.model_validate_json(done.content)
            except ValidationError as exc:
                if done.finish_reason == CUT:
                    cap = self._raised_cap(cap, done)
                    continue
                malformed += 1
                last_error = f"{exc.error_count()} validation errors"
                _log.warning("glimmer_malformed_json", attempt=malformed, error=last_error)
                if malformed >= JSON_ATTEMPTS:
                    msg = f"Glimmer returned malformed JSON twice ({last_error})"
                    raise ModelResponseError(msg) from exc
                continue
            return GlimmerReply(
                answer=answer,
                reasoning_ref=self._reasoning_ref(done),
                finish_reason=done.finish_reason,
                usage=done.usage,
                max_tokens=cap,
                calls=calls,
                latency_ms=max(0, round((self._monotonic() - started) * 1000)),
            )

    def _reasoning_ref(self, done: Completion) -> str | None:
        """The stored reasoning's id (`_send` stored it), or `None` without reasoning or store."""
        if not done.reasoning or self._reasoning is None:
            return None
        return reasoning_id(done.reasoning)

    def _raised_cap(self, cap: int, done: Completion) -> int:
        """The cap to retry a cut reply with.

        Raises:
            ModelTruncatedError: When `cap` is already at the ceiling.
        """
        ceiling = self._config.max_tokens_ceiling
        if cap >= ceiling:
            msg = f"Glimmer reply cut at max_tokens={cap} (the ceiling); usage {done.usage}"
            raise ModelTruncatedError(msg, max_tokens=cap)
        raised = min(cap * 2, ceiling)
        _log.warning("glimmer_reply_cut", max_tokens=cap, retry_max_tokens=raised, **done.usage)
        return raised
