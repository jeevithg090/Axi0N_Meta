"""Llama Guard 4 (12B) client and the plan-time rule screen (`screen_rule_text`).

A rule an owner asks for must be about what people do, never about who they are. Before the
planner compiles a request, `screen_rule_text` sends the request text to Meta's Llama Guard 4 and
refuses it when the model flags one of the identity categories of its hazard taxonomy:

- **S7 Privacy**: rules that would identify, track or expose a particular person;
- **S10 Hate**: rules that single people out by a protected attribute (race, religion, origin,
  disability, gender, ...).

Other categories Llama Guard reports (violence, crimes, ...) are returned for the planner to
show, but do not refuse on their own: a safety rule is often *about* a hazard.

Llama Guard 4 runs on an OpenAI-compatible server (vLLM; `scripts/llama_guard_pod/`): one
`POST /chat/completions` with the request as the user turn; the model answers `safe`, or `unsafe`
and a line of category codes. Calls go through the replay cache and publish `relay.model_used`.
Settings: `LLAMA_GUARD_BASE_URL`, `LLAMA_GUARD_API_KEY` and, behind a RunPod load balancer,
`LLAMA_GUARD_EDGE_KEY` (the RunPod key in `Authorization`, the model key in `X-Rulecam-Key`).
Built with Llama (Llama 4 Community License).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError, ModelResponseError
from rulecam.clients.open_weights.llama_edge import KEY_HEADER
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import HttpxTransport, RetryPolicy, Sender, Transport
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache

__all__ = [
    "CATEGORY_NAMES",
    "IDENTITY_CATEGORIES",
    "GuardVerdict",
    "LlamaGuardClient",
    "parse_guard_output",
    "screen_rule_text",
]

PROVIDER = "llama_guard"
CHAT_PATH = "/chat/completions"
DEFAULT_TIMEOUT_S = 60.0
TEAM_RPM = 600.0
MAX_TOKENS = 20
MAX_TEXT_CHARS = 8000
SEGMENT = "plan"

# Llama Guard 4's hazard taxonomy (MLCommons), as the model card lists it.
CATEGORY_NAMES: Mapping[str, str] = {
    "S1": "Violent Crimes",
    "S2": "Non-Violent Crimes",
    "S3": "Sex-Related Crimes",
    "S4": "Child Sexual Exploitation",
    "S5": "Defamation",
    "S6": "Specialized Advice",
    "S7": "Privacy",
    "S8": "Intellectual Property",
    "S9": "Indiscriminate Weapons",
    "S10": "Hate",
    "S11": "Suicide & Self-Harm",
    "S12": "Sexual Content",
    "S13": "Elections",
    "S14": "Code Interpreter Abuse",
}
# The categories that make a rule about who people are: refused at plan time.
IDENTITY_CATEGORIES = frozenset({"S7", "S10"})
_CODE = re.compile(r"\bS(1[0-4]|[1-9])\b")


@dataclass(frozen=True)
class GuardVerdict:
    """Llama Guard 4's answer for one rule request.

    Attributes:
        safe: The model said `safe`.
        categories: Category codes it flagged (`S7`, `S10`, ...), in its order.
        refused: An identity category (S7 Privacy, S10 Hate) was flagged: decline the rule.
        reason: One plain sentence for the owner (empty when not refused).
        latency_ms: Wall-clock latency of the call.
        model: The model that answered.
    """

    safe: bool
    categories: tuple[str, ...]
    refused: bool
    reason: str
    latency_ms: int
    model: str


def parse_guard_output(text: str) -> tuple[bool, tuple[str, ...]]:
    """Parse Llama Guard's reply: `safe`, or `unsafe` then category codes.

    Args:
        text: The model's message content.

    Returns:
        `(safe, categories)`; categories are de-duplicated, in the order given.

    Raises:
        ModelResponseError: If the reply starts with neither `safe` nor `unsafe`.
    """
    words = text.strip().split()
    first = words[0].lower().strip(".,:") if words else ""
    if first == "safe":
        return True, ()
    if first != "unsafe":
        msg = f"Llama Guard reply is neither safe nor unsafe: {text[:80]!r}"
        raise ModelResponseError(msg)
    codes = [f"S{m}" for m in _CODE.findall(text)]
    return False, tuple(dict.fromkeys(codes))


def _reason(codes: tuple[str, ...]) -> str:
    named = ", ".join(f"{c} {CATEGORY_NAMES[c]}" for c in codes if c in IDENTITY_CATEGORIES)
    return (
        f"Llama Guard 4 flagged this request ({named}): a rule can watch what people do, "
        "never who they are."
    )


class LlamaGuardClient:
    """Classifies text with Llama Guard 4 on an OpenAI-compatible server."""

    def __init__(
        self,
        transport: Transport,
        *,
        model: str = "llama-guard-4-12b",
        bus: Bus | None = None,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Create the client.

        Args:
            transport: The HTTP seam, normally wrapped by the replay cache.
            model: Served model name.
            bus: Bus for `relay.model_used`.
            monotonic: Timer for latency.
        """
        self._sender = Sender(transport, TokenBucket(TEAM_RPM), RetryPolicy(max_attempts=2))
        self._model = model
        self._bus = bus
        self._monotonic = monotonic

    @classmethod
    def from_settings(
        cls, settings: Settings, *, cache: ReplayCache, bus: Bus | None = None
    ) -> LlamaGuardClient:
        """Build a client for `LLAMA_GUARD_BASE_URL`, routed through the replay cache.

        Args:
            settings: Application settings (`LLAMA_GUARD_*`).
            cache: The replay cache.
            bus: Bus for `relay.model_used`.

        Returns:
            The client.

        Raises:
            ClientConfigError: If `LLAMA_GUARD_BASE_URL` is not set.
        """
        if not settings.llama_guard_base_url:
            msg = "LLAMA_GUARD_BASE_URL is not set"
            raise ClientConfigError(msg)
        api_key = settings.llama_guard_api_key
        key = api_key.get_secret_value() if api_key is not None else ""
        headers: dict[str, str] = {}
        bearer = key
        if settings.llama_guard_edge_key is not None:
            bearer = settings.llama_guard_edge_key.get_secret_value()
            headers = {KEY_HEADER: key} if key else {}
        http = HttpxTransport(
            settings.llama_guard_base_url, bearer, timeout_s=DEFAULT_TIMEOUT_S, headers=headers
        )
        model = settings.llama_guard_model
        return cls(cache.wrap_transport(PROVIDER, model, http), model=model, bus=bus)

    @property
    def model(self) -> str:
        """The served model name."""
        return self._model

    def classify(self, text: str) -> tuple[bool, tuple[str, ...], int]:
        """Classify one user turn.

        Args:
            text: The text to screen (cut to `MAX_TEXT_CHARS`).

        Returns:
            `(safe, categories, latency_ms)`.

        Raises:
            ModelResponseError: If the reply cannot be read.
            ModelHTTPError: If the server keeps failing.
            ModelTransportError: On timeouts and connection errors.
        """
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": text[:MAX_TEXT_CHARS]}],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.0,
        }
        started = self._monotonic()
        try:
            reply = self._sender.send({"method": "POST", "path": CHAT_PATH, "json": body})
        finally:
            latency_ms = max(0, round((self._monotonic() - started) * 1000))
            publish_model_used(
                self._bus, ModelUsed(model=self._model, segment=SEGMENT, latency_ms=latency_ms)
            )
        try:
            content = reply.reply.body["choices"][0]["message"]["content"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as exc:
            msg = "Llama Guard reply has no choices[0].message.content"
            raise ModelResponseError(msg) from exc
        safe, codes = parse_guard_output(str(content or ""))
        return safe, codes, latency_ms

    def screen_rule_text(self, text: str) -> GuardVerdict:
        """Screen an owner's rule request before planning (see `screen_rule_text`)."""
        return screen_rule_text(text, self)


def screen_rule_text(text: str, client: LlamaGuardClient) -> GuardVerdict:
    """Refuse a rule request that is about who people are (Llama Guard 4: S7 Privacy, S10 Hate).

    Args:
        text: The owner's request, as typed or transcribed.
        client: The Llama Guard 4 client.

    Returns:
        The verdict; `refused` is true when S7 or S10 was flagged.
    """
    if not text.strip():
        return GuardVerdict(True, (), False, "", 0, client.model)
    safe, codes, latency_ms = client.classify(text)
    refused = any(code in IDENTITY_CATEGORIES for code in codes)
    return GuardVerdict(
        safe=safe,
        categories=codes,
        refused=refused,
        reason=_reason(codes) if refused else "",
        latency_ms=latency_ms,
        model=client.model,
    )
