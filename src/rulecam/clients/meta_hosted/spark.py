"""Muse Spark hosted client (`SparkClient`) implementing `StructuredLLM` (task P2-01).

Uses Meta's Responses API (`POST /responses`) to return structured, schema-validated models.
Features:
- Structured output from `schema.model_json_schema()`.
- `reasoning_effort` and `background=True` (create on Responses API, then poll until completed).
- Function tools and optional web search tool for grounding, capped by `max_tool_calls`.
- Prompt caching token tracking via `cached_tokens`.
- Models: `muse-spark-1.3` (default) and `muse-spark-1.2` (video with audio).
- Validation retry: if response text fails schema validation, retries once with the error,
  raising `StructuredOutputError` if it fails a second time.
- All calls go through the injected `Transport` (and replay cache) and publish `relay.model_used`.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from rulecam.bus import Bus
from rulecam.clients.errors import (
    ClientConfigError,
    ModelResponseError,
    StructuredOutputError,
)
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import HttpxTransport, RetryPolicy, Sender, Transport
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache, get_cache

_log = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)

DEFAULT_MODEL = "muse-spark-1.3"
VIDEO_AUDIO_MODEL = "muse-spark-1.2"
RESPONSES_PATH = "/responses"
DEFAULT_RPM = 2500.0
DEFAULT_SEGMENT = "planner"
POLL_INTERVAL_S = 0.5


def _extract_from_output_item(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    content = item.get("content")
    if isinstance(content, list) and content:
        c0 = content[0]
        if isinstance(c0, dict) and "text" in c0:
            return str(c0["text"])
    if "text" in item:
        return str(item["text"])
    message = item.get("message")
    if isinstance(message, dict) and "content" in message:
        return str(message["content"])
    return None


def extract_output_text(body: object) -> str:
    """Extract raw response text from a Responses API response envelope.

    Args:
        body: Decoded JSON response body.

    Returns:
        The string output from the model.

    Raises:
        ModelResponseError: If no text output can be parsed from the body.
    """
    if isinstance(body, dict):
        if "output_text" in body and isinstance(body["output_text"], str):
            return body["output_text"]
        output = body.get("output")
        if isinstance(output, str):
            return output
        if isinstance(output, list) and output:
            item_text = _extract_from_output_item(output[0])
            if item_text is not None:
                return item_text
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            item_text = _extract_from_output_item(choices[0])
            if item_text is not None:
                return item_text
        if "text" in body and isinstance(body["text"], str):
            return body["text"]
    msg = f"Cannot extract output text from model response: {body!r}"
    raise ModelResponseError(msg)


def extract_cached_tokens(body: object) -> int:
    """Extract cached token count from response usage metadata if present."""
    if isinstance(body, dict):
        usage = body.get("usage")
        if isinstance(usage, dict):
            if "cached_tokens" in usage and isinstance(usage["cached_tokens"], int):
                return usage["cached_tokens"]
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                cached = details.get("cached_tokens")
                if isinstance(cached, int):
                    return cached
    return 0


class SparkClient:
    """Muse Spark client on Meta's hosted Responses API; satisfies `StructuredLLM`."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock,
        bus: Bus | None = None,
        cache: ReplayCache | None = None,
        rpm: float = DEFAULT_RPM,
        segment: str = DEFAULT_SEGMENT,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        seed: int | None = None,
        poll_interval_s: float = POLL_INTERVAL_S,
    ) -> None:
        """Create the client.

        Args:
            transport: The HTTP transport callable (mapping in -> mapping out).
            clock: Time source for timestamps.
            bus: Event bus for `relay.model_used`.
            cache: Replay cache instance, if wrapping calls directly.
            rpm: Maximum requests per minute.
            segment: Pipeline segment name for relay telemetry.
            retry: Retry policy for 429/5xx responses.
            sleep: Sleep callable for rate limiting and backoff.
            monotonic: Monotonic clock callable.
            seed: Seed for retry jitter.
            poll_interval_s: Interval between polling attempts for background tasks.
        """
        self._clock = clock
        self._bus = bus
        self._cache = cache
        self._segment = segment
        self._sleep = sleep
        self._monotonic = monotonic
        self._poll_interval_s = poll_interval_s
        self._last_cached_tokens = 0
        self._total_cached_tokens = 0

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
    ) -> SparkClient:
        """Construct a SparkClient configured from application settings.

        Args:
            settings: Runtime settings.
            clock: Clock source.
            bus: Bus for relay events.
            cache: Replay cache.

        Returns:
            A configured SparkClient.

        Raises:
            ClientConfigError: When credentials or URLs are missing in live/record modes.
        """
        if settings.rulecam_mode == "replay" and cache is None:
            cache = get_cache()

        if settings.rulecam_mode != "replay" and (
            not settings.meta_api_base_url or settings.meta_api_key is None
        ):
            msg = "META_API_BASE_URL and META_API_KEY are required for hosted Spark client"
            raise ClientConfigError(msg)

        base_url = settings.meta_api_base_url or "https://api.meta.ai/v1"
        api_key = settings.meta_api_key.get_secret_value() if settings.meta_api_key else "replay"
        raw_transport = HttpxTransport(base_url, api_key)
        transport = (
            cache.wrap_transport("meta", DEFAULT_MODEL, raw_transport) if cache else raw_transport
        )
        return cls(transport, clock=clock, bus=bus, cache=cache)

    @property
    def cached_tokens(self) -> int:
        """Cached tokens reported in the most recent response."""
        return self._last_cached_tokens

    @property
    def total_cached_tokens(self) -> int:
        """Cumulative cached tokens reported across all calls."""
        return self._total_cached_tokens

    def _build_request_body(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[BaseModel],
        reasoning_effort: Literal["low", "medium", "high"],
        background: bool,
        tools: list[dict[str, object]] | None,
        enable_web_search: bool,
        max_tool_calls: int | None,
    ) -> dict[str, object]:
        effective_tools: list[dict[str, object]] = list(tools) if tools else []
        if enable_web_search and not any(t.get("type") == "web_search" for t in effective_tools):
            effective_tools.append({"type": "web_search"})

        body: dict[str, object] = {
            "model": model,
            "input": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
            "reasoning_effort": reasoning_effort,
        }
        if background:
            body["background"] = True
        if effective_tools:
            body["tools"] = effective_tools
        if max_tool_calls is not None:
            body["max_tool_calls"] = max_tool_calls
        return body

    def _poll_background(self, resp_id: str) -> tuple[dict[str, Any], int]:
        attempts = 0
        while True:
            self._sleep(self._poll_interval_s)
            poll_req: dict[str, object] = {
                "method": "GET",
                "path": f"{RESPONSES_PATH}/{resp_id}",
            }
            poll_reply = self._sender.send(poll_req)
            attempts += poll_reply.attempts
            poll_body = poll_reply.reply.body
            if not isinstance(poll_body, dict):
                msg = f"Expected JSON object from polling response {resp_id}"
                raise ModelResponseError(msg)
            status = poll_body.get("status")
            if status == "completed":
                return poll_body, attempts
            if status == "failed":
                msg = f"Background response {resp_id} failed: {poll_body.get('error')}"
                raise ModelResponseError(msg)

    def _send_and_poll(
        self, body: dict[str, object], model: str, camera_id: str | None
    ) -> tuple[dict[str, Any], int, int]:
        started = self._monotonic()
        req: dict[str, object] = {"method": "POST", "path": RESPONSES_PATH, "json": body}
        sender_reply = self._sender.send(req)
        attempts = sender_reply.attempts
        reply_body = sender_reply.reply.body

        if not isinstance(reply_body, dict):
            msg = f"Expected JSON object reply from Responses API, got {type(reply_body)}"
            raise ModelResponseError(msg)

        if body.get("background") is True and reply_body.get("status") in (
            "in_progress",
            "queued",
            "pending",
        ):
            resp_id = reply_body.get("id")
            if not resp_id:
                msg = "Background response missing response 'id'"
                raise ModelResponseError(msg)
            reply_body, poll_attempts = self._poll_background(resp_id)
            attempts += poll_attempts

        latency_ms = max(0, round((self._monotonic() - started) * 1000))
        publish_model_used(
            self._bus,
            ModelUsed(
                model=model,
                segment=self._segment,
                latency_ms=latency_ms,
                camera_id=camera_id,
                attempts=attempts,
            ),
        )
        return reply_body, latency_ms, attempts

    def structured(
        self,
        *,
        model: str = DEFAULT_MODEL,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
        tools: list[dict[str, object]] | None = None,
        enable_web_search: bool = False,
        max_tool_calls: int | None = None,
        camera_id: str | None = None,
    ) -> M:
        """Call Muse Spark and return validated structured output conforming to `schema`.

        Args:
            model: Model identifier (`muse-spark-1.3` default or `muse-spark-1.2` for video+audio).
            messages: Input chat messages.
            schema: Pydantic model class to validate the response against.
            reasoning_effort: Reasoning effort level ('low', 'medium', 'high').
            background: Whether to run in the background and poll for completion.
            tools: Function or tool definitions.
            enable_web_search: Whether to enable web search grounding.
            max_tool_calls: Optional cap on tool execution iterations.
            camera_id: Camera identifier for telemetry.

        Returns:
            The parsed and validated pydantic model instance.

        Raises:
            StructuredOutputError: If the model output fails validation after a retry.
            ModelHTTPError: If the server returns a persistent HTTP error.
            ModelResponseError: If the server response cannot be decoded.
        """
        body = self._build_request_body(
            model=model,
            messages=messages,
            schema=schema,
            reasoning_effort=reasoning_effort,
            background=background,
            tools=tools,
            enable_web_search=enable_web_search,
            max_tool_calls=max_tool_calls,
        )

        reply_body, _, _ = self._send_and_poll(body, model, camera_id)
        cached = extract_cached_tokens(reply_body)
        self._last_cached_tokens = cached
        self._total_cached_tokens += cached

        text = extract_output_text(reply_body)
        first_error_msg = ""
        try:
            return schema.model_validate_json(text)
        except (ValidationError, ValueError) as first_err:
            first_error_msg = str(first_err)
            _log.warning("spark_structured_validation_failed_attempt_1", error=first_error_msg)

        # Retry once with validation error feedback appended
        retry_messages = list(messages)
        retry_messages.append({"role": "assistant", "content": text})
        retry_messages.append(
            {
                "role": "user",
                "content": (
                    f"The previous response failed schema validation: {first_error_msg}. "
                    "Please return only valid JSON matching the required schema."
                ),
            }
        )
        retry_body = self._build_request_body(
            model=model,
            messages=retry_messages,
            schema=schema,
            reasoning_effort=reasoning_effort,
            background=background,
            tools=tools,
            enable_web_search=enable_web_search,
            max_tool_calls=max_tool_calls,
        )
        retry_reply_body, _, _ = self._send_and_poll(retry_body, model, camera_id)
        retry_text = extract_output_text(retry_reply_body)
        try:
            return schema.model_validate_json(retry_text)
        except (ValidationError, ValueError) as second_err:
            _log.error("spark_structured_validation_failed_attempt_2", error=str(second_err))
            msg = f"Model output failed validation twice: {second_err}"
            raise StructuredOutputError(msg) from second_err
