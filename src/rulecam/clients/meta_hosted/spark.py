"""Muse Spark hosted client (`SparkClient`), the hosted `StructuredLLM` (task P2-01).

One `POST /responses` per call, the same Responses + Files shape as hosted SAM 3.1 (see
`responses.py`). Request fields follow the Responses schemas reference (read Oct 31 2026):

- structured output in `text.format` (`type: json_schema`, `name`, `schema`). `strict` is off:
  pydantic schemas use defaults, open dicts and root unions, which the strict subset rejects, so
  the reply is validated here instead, with one retry that carries the validation errors. Every
  object property is sent as `required` (`answer_schema`): with fields left optional, the model
  answered from the schema's minimum, e.g. one concern with no concepts when asked for two;
- `reasoning.effort`; `background: true` creates the response, then `GET /responses/{id}` is
  polled until it leaves `queued`/`in_progress` (background cannot be combined with `stream`, so
  every call sends `stream: false` unless it is a background call);
- `tools` (function tools, a `{"type": "web_search"}` grounding tool), capped by
  `max_tool_calls`. `tool_choice` is never sent: the API accepts `"auto"` only, its default.
  `grounded` also asks for `include: ["web_search_call.results"]` and returns the pages each
  search retrieved and the cited URLs with the answer (`grounding.py`);
- media goes in as content parts that reference `POST /files` uploads (`upload`, `file_part`);
- `usage.input_tokens_details.cached_tokens` is recorded so the stable system prefix can be
  checked for prompt-cache hits.

Models: `muse-spark-1.3` by default, `muse-spark-1.2` for video with narration (1.3 audio is
degraded). Every call goes through the injected transport, which `from_settings` always wraps in
the replay cache, and publishes `relay.model_used`.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from rulecam.bus import Bus
from rulecam.clients.errors import ModelResponseError, StructuredOutputError
from rulecam.clients.meta_hosted.grounding import INCLUDE_RESULTS, Grounded, grounded
from rulecam.clients.meta_hosted.responses import (
    PENDING_STATUSES,
    REPLAY_PROVIDER,
    RESPONSES_PATH,
    FileUploader,
    TimedReply,
    cached_tokens,
    file_part,
    latency_ms,
    live_transport,
    output_text,
    response_body,
)
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import RetryPolicy, Sender, Transport
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)

DEFAULT_MODEL = "muse-spark-1.3"
VIDEO_AUDIO_MODEL = "muse-spark-1.2"
DEFAULT_RPM = 2500.0
DEFAULT_SEGMENT = "planner"
POLL_INTERVAL_S = 1.0
POLL_TIMEOUT_S = 600.0
WEB_SEARCH_TOOL: dict[str, object] = {"type": "web_search"}

Effort = Literal["low", "medium", "high"]


def answer_schema(schema: type[BaseModel]) -> dict[str, object]:
    """Return `schema`'s JSON schema with every object property marked `required`.

    Defaults still validate (a field the model fills with `null`, `[]` or `{}` is accepted); the
    model just may no longer leave a field out.
    """
    required: dict[str, object] = _require_all(schema.model_json_schema())
    return required


def _require_all(node: object) -> Any:
    if isinstance(node, dict):
        out = {key: _require_all(value) for key, value in node.items()}
        properties = out.get("properties")
        if isinstance(properties, dict) and properties:
            out["required"] = list(properties)
        return out
    if isinstance(node, list):
        return [_require_all(item) for item in node]
    return node


def build_request(
    *,
    model: str,
    messages: list[dict[str, object]],
    schema: type[BaseModel],
    reasoning_effort: Effort,
    background: bool,
    tools: list[dict[str, object]] | None = None,
    web_search: bool = False,
    max_tool_calls: int | None = None,
    web_search_options: Mapping[str, object] | None = None,
    include: list[str] | None = None,
) -> dict[str, object]:
    """Build the `POST /responses` body for one structured call.

    Args:
        model: The model id.
        messages: Input items (`{"role", "content"}`; content is text or a list of parts).
        schema: The pydantic model the output must validate against.
        reasoning_effort: `reasoning.effort`.
        background: Whether to run as a background response.
        tools: Function or other tool definitions, passed through unchanged.
        web_search: Whether to add the `web_search` grounding tool.
        max_tool_calls: Cap on tool calls; must be at least 1 when given.
        web_search_options: Extra fields of the `web_search` tool (`user_location`,
            `search_context_size`); only with `web_search`.
        include: Extra output to return, e.g. `web_search_call.results`.

    Returns:
        The request body.

    Raises:
        ValueError: If `max_tool_calls` is below 1 or a tool sets `tool_choice`.
    """
    all_tools = list(tools or [])
    if any("tool_choice" in tool for tool in all_tools):
        msg = "tool_choice is 'auto' only on the hosted API; do not set it"
        raise ValueError(msg)
    if web_search and not any(t.get("type") == "web_search" for t in all_tools):
        all_tools.append({**WEB_SEARCH_TOOL, **(web_search_options or {})})
    body: dict[str, object] = {
        "model": model,
        "input": messages,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema.__name__,
                "schema": answer_schema(schema),
                "strict": False,
            }
        },
        "reasoning": {"effort": reasoning_effort},
    }
    if background:
        body["background"] = True
    else:
        body["stream"] = False
    if all_tools:
        body["tools"] = all_tools
    if max_tool_calls is not None:
        if max_tool_calls < 1:
            msg = f"max_tool_calls must be at least 1, got {max_tool_calls}"
            raise ValueError(msg)
        body["max_tool_calls"] = max_tool_calls
    if include:
        body["include"] = list(include)
    return body


def _validate[T: BaseModel](schema: type[T], text: str) -> T:
    """Validate `text` as `schema`; if prose surrounds the JSON object, validate the object.

    Raises:
        ValidationError: When neither the text nor the outermost `{...}` in it validates.
    """
    try:
        return schema.model_validate_json(text)
    except ValidationError:
        start, end = text.find("{"), text.rfind("}")
        whole = start == 0 and end == len(text.rstrip()) - 1
        if start < 0 or end <= start or whole:
            raise
        return schema.model_validate_json(text[start : end + 1])


def _retry_messages(
    messages: list[dict[str, object]], text: str, error: str
) -> list[dict[str, object]]:
    return [
        *messages,
        {"role": "assistant", "content": text},
        {
            "role": "user",
            "content": (
                f"The previous response failed schema validation: {error}. "
                "Return only JSON that matches the required schema."
            ),
        },
    ]


class SparkClient:
    """Muse Spark on Meta's hosted Responses API; satisfies `StructuredLLM`."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock,
        bus: Bus | None = None,
        rpm: float = DEFAULT_RPM,
        segment: str = DEFAULT_SEGMENT,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        seed: int | None = None,
        poll_interval_s: float = POLL_INTERVAL_S,
        poll_timeout_s: float = POLL_TIMEOUT_S,
    ) -> None:
        """Create the client.

        Args:
            transport: Sends one request mapping and returns the reply mapping.
            clock: Time source.
            bus: Bus for `relay.model_used`; `None` publishes nothing.
            rpm: Request budget per minute for this client.
            segment: Pipeline segment shown on the relay HUD.
            retry: Backoff policy for 429/5xx (default: 4 tries).
            sleep: Blocking sleep (injected in tests).
            monotonic: Monotonic time source (injected in tests).
            seed: Seed for the retry jitter.
            poll_interval_s: Seconds between polls of a background response.
            poll_timeout_s: Seconds after which a background response is abandoned.
        """
        self._clock = clock
        self._bus = bus
        self._segment = segment
        self._sleep = sleep
        self._monotonic = monotonic
        self._poll_interval_s = poll_interval_s
        self._poll_timeout_s = poll_timeout_s
        self._last_cached_tokens = 0
        self._total_cached_tokens = 0
        limiter = TokenBucket(rpm, monotonic=monotonic, sleep=sleep)
        jitter = random.Random(seed)  # noqa: S311 - retry jitter, not cryptography
        self._sender = Sender(transport, limiter, retry or RetryPolicy(), sleep, jitter)
        self._files = FileUploader(self._sender)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Clock,
        bus: Bus | None = None,
        cache: ReplayCache | None = None,
    ) -> SparkClient:
        """Build a client whose transport always goes through the replay cache.

        Args:
            settings: Application settings.
            clock: Time source.
            bus: Bus for `relay.model_used` (and `mode.changed` of a cache built here).
            cache: The replay cache; by default one is built from `settings`.

        Returns:
            The client.

        Raises:
            ClientConfigError: In `live` or `record` mode without `META_API_BASE_URL` and
                `META_API_KEY`.
        """
        if cache is None:
            cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
        inner = live_transport(settings, required=cache.mode != "replay", what="Muse Spark")
        transport = cache.wrap_transport(REPLAY_PROVIDER, DEFAULT_MODEL, inner)
        return cls(transport, clock=clock, bus=bus)

    @property
    def cached_tokens(self) -> int:
        """Cached input tokens reported by the most recent response."""
        return self._last_cached_tokens

    @property
    def total_cached_tokens(self) -> int:
        """Cached input tokens summed over every response."""
        return self._total_cached_tokens

    def upload(self, path: Path) -> str:
        """Upload a PDF, image, audio or video file once and return its file id.

        Args:
            path: The local file.

        Returns:
            The file id to reference with `file_part`.

        Raises:
            ValueError: If the Files API does not accept the file type.
            ModelHTTPError: When the upload keeps failing or is rejected.
            ModelResponseError: When the reply has no file id.
        """
        return self._files.upload(path)

    def attach(self, path: Path) -> dict[str, object]:
        """Upload `path` and return the content part that references it.

        Args:
            path: The local file.

        Returns:
            An `input_image`, `input_video`, `input_audio` or `input_file` part.
        """
        return file_part(path, self.upload(path))

    def structured(
        self,
        *,
        model: str = DEFAULT_MODEL,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Effort = "low",
        background: bool = False,
        tools: list[dict[str, object]] | None = None,
        enable_web_search: bool = False,
        max_tool_calls: int | None = None,
        camera_id: str | None = None,
    ) -> M:
        """Call Muse Spark and return its output validated against `schema`.

        Args:
            model: `muse-spark-1.3` (default) or `muse-spark-1.2` for video with audio.
            messages: Input items; keep the system message first and stable for prompt caching.
            schema: The pydantic model to validate against.
            reasoning_effort: `reasoning.effort`.
            background: Run as a background response and poll until it finishes.
            tools: Function or other tool definitions.
            enable_web_search: Add the `web_search` grounding tool.
            max_tool_calls: Cap on tool calls.
            camera_id: Camera the call is for, for the relay HUD.

        Returns:
            The validated model.

        Raises:
            StructuredOutputError: If the output fails validation twice.
            ModelHTTPError: When the API keeps failing or rejects the request.
            ModelResponseError: When a reply cannot be understood or the response failed.
        """
        request = partial(
            build_request,
            model=model,
            schema=schema,
            reasoning_effort=reasoning_effort,
            background=background,
            tools=tools,
            web_search=enable_web_search,
            max_tool_calls=max_tool_calls,
        )
        text = self._call(request(messages=messages), model, camera_id)
        try:
            return _validate(schema, text)
        except ValidationError as first:
            _log.warning("spark_validation_retry", model=model, errors=first.error_count())
            retry = _retry_messages(messages, text, str(first))
        text = self._call(request(messages=retry), model, camera_id)
        try:
            return _validate(schema, text)
        except ValidationError as second:
            msg = f"Spark output failed validation twice against {schema.__name__}: {second}"
            raise StructuredOutputError(msg) from second

    def grounded(
        self,
        *,
        model: str = DEFAULT_MODEL,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Effort = "low",
        user_location: Mapping[str, str] | None = None,
        search_context_size: Literal["low", "medium", "high"] | None = None,
        max_tool_calls: int | None = None,
    ) -> Grounded[M]:
        """Call Muse Spark with `web_search` and return its answer with what the search returned.

        Like `structured` (one retry with the validation errors), but the reply's raw search
        results (`include: ["web_search_call.results"]`) and `url_citation` URLs come back with
        the answer, so a caller can check each claimed source against the pages actually
        retrieved (`Grounded.retrieved`).

        Args:
            model: The model id.
            messages: Input items.
            schema: The pydantic model to validate against.
            reasoning_effort: `reasoning.effort`.
            user_location: The `web_search` tool's approximate location (`country` as ISO
                3166-1 alpha-2, `region`, `city`, `timezone`); fields left out are not sent.
            search_context_size: How much retrieved content reaches the model.
            max_tool_calls: Cap on tool calls.

        Returns:
            The validated answer with its sources and citations.

        Raises:
            StructuredOutputError: If the output fails validation twice.
            ModelHTTPError: When the API keeps failing or rejects the request.
            ModelResponseError: When a reply cannot be understood or the response failed.
        """
        options: dict[str, object] = {}
        if user_location:
            options["user_location"] = {"type": "approximate", **dict(user_location)}
        if search_context_size is not None:
            options["search_context_size"] = search_context_size
        request = partial(
            build_request,
            model=model,
            schema=schema,
            reasoning_effort=reasoning_effort,
            background=False,
            web_search=True,
            web_search_options=options,
            include=[INCLUDE_RESULTS],
            max_tool_calls=max_tool_calls,
        )
        bodies = [self._call_body(request(messages=messages), model, None)]
        text = output_text(bodies[0])
        try:
            return grounded(_validate(schema, text), bodies)
        except ValidationError as first:
            _log.warning("spark_validation_retry", model=model, errors=first.error_count())
            retry = _retry_messages(messages, text, str(first))
        bodies.append(self._call_body(request(messages=retry), model, None))
        try:
            return grounded(_validate(schema, output_text(bodies[-1])), bodies)
        except ValidationError as second:
            msg = f"Spark output failed validation twice against {schema.__name__}: {second}"
            raise StructuredOutputError(msg) from second

    # --- internals ------------------------------------------------------------------------------

    def _send(self, request: dict[str, object]) -> TimedReply:
        start = self._monotonic()
        result = self._sender.send(request)
        return TimedReply(
            body=result.reply.body,
            attempts=result.attempts,
            latency_ms=latency_ms(result, self._monotonic() - start),
        )

    def _call(self, body: dict[str, object], model: str, camera_id: str | None) -> str:
        return output_text(self._call_body(body, model, camera_id))

    def _call_body(
        self, body: dict[str, object], model: str, camera_id: str | None
    ) -> Mapping[str, object]:
        start = self._monotonic()
        reply = self._send({"method": "POST", "path": RESPONSES_PATH, "json": body})
        attempts, recorded_ms = reply.attempts, reply.latency_ms
        final = response_body(reply.body)
        if final.get("status") in PENDING_STATUSES:
            final, poll_attempts, poll_ms = self._poll(final)
            attempts += poll_attempts
            recorded_ms += poll_ms
        elapsed_ms = max(0, round((self._monotonic() - start) * 1000))
        tokens = cached_tokens(final)
        self._last_cached_tokens = tokens
        self._total_cached_tokens += tokens
        publish_model_used(
            self._bus,
            ModelUsed(
                model=model,
                segment=self._segment,
                latency_ms=max(elapsed_ms, recorded_ms),
                camera_id=camera_id,
                attempts=attempts,
            ),
        )
        _log.info("spark_call", model=model, attempts=attempts, cached_tokens=tokens)
        return final

    def _poll(self, created: Mapping[str, object]) -> tuple[Mapping[str, object], int, int]:
        response_id = created.get("id")
        if not isinstance(response_id, str) or not response_id:
            msg = "background response has no id to poll"
            raise ModelResponseError(msg)
        deadline = self._monotonic() + self._poll_timeout_s
        attempts = recorded_ms = 0
        body: Mapping[str, object] = created
        while body.get("status") in PENDING_STATUSES:
            if self._monotonic() > deadline:
                msg = f"background response {response_id} still pending after poll timeout"
                raise ModelResponseError(msg)
            self._sleep(self._poll_interval_s)
            reply = self._send({"method": "GET", "path": f"{RESPONSES_PATH}/{response_id}"})
            attempts += reply.attempts
            recorded_ms += reply.latency_ms
            body = response_body(reply.body)
        return body, attempts, recorded_ms
