"""Muse Spark Open client on Baseten (`SparkOpenClient`, task P3-04).

Baseten serves Spark Open behind an OpenAI-compatible chat API (`POST /chat/completions`,
`Authorization: Api-Key …`). Spark Open always reasons before answering; its
`message.reasoning_content` is captured **verbatim**, stored under `data/reasoning/<id>.txt`, and
referenced by id, so the UI can show the model thinking (MP §3.4).

Every call asks for JSON-schema-constrained output, goes through the replay cache, publishes
`relay.model_used`, and appends a line (with the Baseten response id) to
`evidence/spark_open/inference-log.jsonl`. Whether the deployment accepts images is a setting
(`SPARK_OPEN_IMAGES_SUPPORTED`); when it does not, callers send text only.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from rulecam.bus import Bus
from rulecam.clients.errors import ModelResponseError
from rulecam.clients.open_weights.evidence_log import EvidenceLog, InferenceLine
from rulecam.clients.open_weights.glimmer import request_hash
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import HttpxTransport, RetryPolicy, Sender, Transport
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)

PROVIDER = "baseten"
CHAT_PATH = "/chat/completions"
DEFAULT_TIMEOUT_S = 90.0
DEFAULT_MAX_TOKENS = 4096
TEAM_RPM = 120.0
AUTH_SCHEME = "Api-Key"
REASONING_ID_HEX = 16
Effort = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class SparkReply[A: BaseModel]:
    """A validated answer plus Spark Open's reasoning.

    Attributes:
        answer: The reply, validated against the requested schema.
        reasoning: `reasoning_content`, verbatim (empty if the server sent none).
        reasoning_ref: Id of the stored reasoning file (`data/reasoning/<id>.txt`).
        request_id: Baseten's response id, when present.
        latency_ms: Wall-clock latency including retries.
    """

    answer: A
    reasoning: str
    reasoning_ref: str
    request_id: str | None
    latency_ms: int


@dataclass(frozen=True)
class SparkOpenConfig:
    """Deployment facts.

    Attributes:
        model: Baseten model slug.
        images_supported: Whether image parts are accepted (checked on day one).
        max_tokens: Output cap, including reasoning.
        segment: Relay HUD segment name.
    """

    model: str = "meta-models/muse-spark-open-1026"
    images_supported: bool = False
    max_tokens: int = DEFAULT_MAX_TOKENS
    segment: str = "verify"


def _parse(body: object) -> tuple[str, str, str | None]:
    """Return (content, reasoning, response id) from a chat-completion body."""
    try:
        message = body["choices"][0]["message"]  # type: ignore[index]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as exc:
        msg = "Spark Open reply has no choices[0].message.content"
        raise ModelResponseError(msg) from exc
    if not isinstance(content, str):
        msg = "Spark Open reply content is not text"
        raise ModelResponseError(msg)
    reasoning = message.get("reasoning_content") or ""
    response_id = body.get("id") if isinstance(body, Mapping) else None
    return content, str(reasoning), str(response_id) if response_id else None


def reasoning_id(text: str) -> str:
    """Return the content-addressed id of a reasoning text (`so_` + 16 hex of its SHA-256).

    The same reasoning always gets the same id, so replayed runs are byte-identical (§5.4).
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"so_{digest[:REASONING_ID_HEX]}"


class ReasoningStore:
    """Writes reasoning text to `<directory>/<id>.txt` and returns the id."""

    def __init__(self, directory: Path) -> None:
        """Create the store.

        Args:
            directory: Usually `data/reasoning`.
        """
        self._directory = directory

    def save(self, text: str) -> str:
        """Store `text` under its content-addressed id and return the id."""
        reasoning_id_ = reasoning_id(text)
        self._directory.mkdir(parents=True, exist_ok=True)
        (self._directory / f"{reasoning_id_}.txt").write_text(text, encoding="utf-8")
        return reasoning_id_

    def load(self, reasoning_id: str) -> str:
        """Return stored reasoning by id.

        Raises:
            ValueError: If the id is not a plain stored-reasoning id.
            FileNotFoundError: If nothing is stored under it.
        """
        if not reasoning_id.startswith("so_") or not reasoning_id[3:].isalnum():
            msg = f"invalid reasoning id {reasoning_id!r}"
            raise ValueError(msg)
        return (self._directory / f"{reasoning_id}.txt").read_text(encoding="utf-8")


class SparkOpenClient:
    """Structured calls to Muse Spark Open with captured reasoning."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock,
        reasoning: ReasoningStore,
        config: SparkOpenConfig | None = None,
        bus: Bus | None = None,
        evidence: EvidenceLog | None = None,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Create the client.

        Args:
            transport: The HTTP seam, normally wrapped by the replay cache.
            clock: Time source for evidence lines.
            reasoning: Where reasoning text is stored.
            config: Model slug, image support and limits.
            bus: Bus for `relay.model_used`.
            evidence: Open-weight evidence log.
            monotonic: Timer for latency.
        """
        self._config = config or SparkOpenConfig()
        self._sender = Sender(transport, TokenBucket(TEAM_RPM), RetryPolicy(max_attempts=2))
        self._clock = clock
        self._reasoning = reasoning
        self._bus = bus
        self._evidence = evidence
        self._monotonic = monotonic

    @classmethod
    def from_settings(
        cls, settings: Settings, *, cache: ReplayCache, clock: Clock, bus: Bus | None = None
    ) -> SparkOpenClient:
        """Build a Baseten client routed through the replay cache.

        Args:
            settings: Application settings (`BASETEN_*`, `SPARK_OPEN_IMAGES_SUPPORTED`).
            cache: The replay cache.
            clock: Time source.
            bus: Bus for `relay.model_used`.

        Returns:
            The client. Without `BASETEN_API_KEY` it can still replay recorded calls.
        """
        key = settings.baseten_api_key.get_secret_value() if settings.baseten_api_key else ""
        config = SparkOpenConfig(
            model=settings.baseten_spark_open_model,
            images_supported=settings.spark_open_images_supported,
        )
        http = HttpxTransport(
            settings.baseten_base_url, key, timeout_s=DEFAULT_TIMEOUT_S, auth_scheme=AUTH_SCHEME
        )
        return cls(
            cache.wrap_transport(PROVIDER, config.model, http),
            clock=clock,
            reasoning=ReasoningStore(settings.rulecam_data_dir / "reasoning"),
            config=config,
            bus=bus,
            evidence=EvidenceLog(settings.evidence_dir / "spark_open" / "inference-log.jsonl"),
        )

    @property
    def model(self) -> str:
        """The Baseten model slug."""
        return self._config.model

    @property
    def images_supported(self) -> bool:
        """Whether image parts may be sent."""
        return self._config.images_supported

    def reasoning_text(self, reasoning_ref: str) -> str:
        """Return stored reasoning by its reference."""
        return self._reasoning.load(reasoning_ref)

    def _body(
        self, messages: list[dict[str, object]], schema: type[BaseModel], effort: Effort
    ) -> dict[str, object]:
        return {
            "model": self._config.model,
            "messages": messages,
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

    def reason(
        self,
        messages: list[dict[str, object]],
        schema: type[M],
        *,
        effort: Effort = "low",
        camera_id: str | None = None,
    ) -> SparkReply[M]:
        """Ask Spark Open for JSON matching `schema`, keeping its reasoning.

        Args:
            messages: OpenAI-style chat messages.
            schema: The pydantic model the answer must validate against.
            effort: Reasoning effort; interactive paths use `low` or `medium`.
            camera_id: Camera the call is for, shown on the relay HUD.

        Returns:
            The answer, the verbatim reasoning and its stored reference.

        Raises:
            ModelResponseError: If the reply is missing or does not match the schema.
            ModelHTTPError: If the API keeps failing.
            ModelTransportError: On timeouts and connection errors.
        """
        body = self._body(messages, schema, effort)
        started = self._monotonic()
        request_id: str | None = None
        ok = False
        try:
            reply = self._sender.send({"method": "POST", "path": CHAT_PATH, "json": body})
            content, reasoning, request_id = _parse(reply.reply.body)
            answer = schema.model_validate_json(content)
            ok = True
        except ValidationError as exc:
            msg = f"Spark Open answer does not match {schema.__name__}"
            raise ModelResponseError(msg) from exc
        finally:
            latency_ms = max(0, round((self._monotonic() - started) * 1000))
            self._record(body, latency_ms, ok=ok, request_id=request_id, camera_id=camera_id)
        reasoning_ref = self._reasoning.save(reasoning)
        return SparkReply(answer, reasoning, reasoning_ref, request_id, latency_ms)

    def _record(
        self,
        body: Mapping[str, object],
        latency_ms: int,
        *,
        ok: bool,
        request_id: str | None,
        camera_id: str | None,
    ) -> None:
        publish_model_used(
            self._bus,
            ModelUsed(
                model=self._config.model,
                segment=self._config.segment,
                latency_ms=latency_ms,
                camera_id=camera_id,
            ),
        )
        if self._evidence is not None:
            self._evidence.append(
                InferenceLine(
                    ts=self._clock.now(),
                    model=self._config.model,
                    hardware="baseten",
                    latency_ms=latency_ms,
                    request_hash=request_hash(body),
                    ok=ok,
                    request_id=request_id,
                )
            )
