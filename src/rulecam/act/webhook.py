"""Signed outbound webhooks (add-on 5.4): every alert as JSON to Slack, Teams, n8n, Zapier, ….

`WebhookNotifier` wraps the outbound notifier. After the alert is stored (and sent to phones), it
queues one delivery per configured target, and `WebhookDispatcher.poll` (an API poller, off the
event loop) sends it. A slow or failing receiver never delays an alert or the escalation timers.

**Targets** (`RULECAM_WEBHOOKS`, kept only in `.env` because the URLs are secrets):
`preset=url` pairs, comma-separated. The presets are:

- `generic`: Rulecam's JSON (`type`, `id`, `site`, `event_id`, `rule_id`, `role`, `title`,
  `body`, `ts`, `link`), for n8n, Zapier, Home Assistant or your own code;
- `slack`: a Slack incoming-webhook message (`text` plus Block Kit blocks);
- `teams`: an Adaptive Card for a Teams Workflows "When a Teams webhook request is received"
  flow. Teams' old Office 365 connectors were retired, so this replaces them.

**Signing.** With `RULECAM_WEBHOOK_SECRET` set, every request carries
`X-Rulecam-Timestamp: <unix seconds>` and
`X-Rulecam-Signature: v1=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>`. The signature is
recomputed at each attempt, so a receiver can reject anything older than a few minutes
(`verify_signature`). A `generic` target without a secret is refused at start-up: anyone could
post to it otherwise. Slack and Teams URLs are their own secret, and they ignore the headers.

**Idempotency.** `Idempotency-Key` is the event hash (SHA-256 of the canonical generic payload).
It stays the same on every retry, so a receiver can drop duplicates. The dispatcher also skips a
key it has already queued for the same target.

**Retries.** A 2xx is delivered. A 408, 429, 5xx or network error is retried with exponential
backoff (2 s, 4 s, 8 s, … up to 5 min) for up to 6 attempts. Any other status is final. Every
outcome is logged with the preset and the key, never the URL. The queue is bounded: when it is
full, the oldest delivery is dropped (logged).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal, get_args

import httpx

from rulecam.act.notify import alert_event, alert_rule
from rulecam.act.whatsapp import SeenIds
from rulecam.contracts import Clock, Frozen, Notifier
from rulecam.errors import RulecamError
from rulecam.log import get_logger

_log = get_logger(__name__)

Preset = Literal["generic", "slack", "teams"]
PRESETS: Final = frozenset(get_args(Preset))
SIGNATURE_HEADER: Final = "X-Rulecam-Signature"
TIMESTAMP_HEADER: Final = "X-Rulecam-Timestamp"
IDEMPOTENCY_HEADER: Final = "Idempotency-Key"
EVENT_HEADER: Final = "X-Rulecam-Event"
SIGNATURE_VERSION: Final = "v1"
POST_TIMEOUT_S: Final = 5.0
MAX_ATTEMPTS: Final = 6
BASE_DELAY_S: Final = 2.0
MAX_DELAY_S: Final = 300.0
QUEUE_CAPACITY: Final = 500
TOLERANCE_S: Final = 300
WAIT_S: Final = 1.0
RETRYABLE: Final = frozenset({408, 429})
HTTP_OK_MIN: Final = 200
HTTP_OK_MAX: Final = 299
HTTP_SERVER_ERROR_MIN: Final = 500
SLACK_HEADER_CHARS: Final = 150
SLACK_SECTION_CHARS: Final = 3000

# Posts a body to a URL with headers and returns the HTTP status (raises on a network error).
Poster = Callable[[str, bytes, Mapping[str, str]], int]


class WebhookError(RulecamError):
    """The webhook configuration is invalid."""


class WebhookTarget(Frozen):
    """One receiver. `url` is a secret: it is never logged or served."""

    preset: Preset
    url: str


class AlertEvent(Frozen):
    """What one alert says, before it is shaped for a preset."""

    site: str
    event_id: str | None
    rule_id: str | None
    role: str
    title: str
    body: str
    ts: datetime
    link: str | None = None


def parse_targets(spec: str) -> tuple[WebhookTarget, ...]:
    """Parse `preset=url,preset=url` (`RULECAM_WEBHOOKS`).

    Raises:
        WebhookError: For an unknown preset or a URL that is not http(s).
    """
    targets: list[WebhookTarget] = []
    for pair in filter(None, (p.strip() for p in spec.split(","))):
        preset, sep, url = pair.partition("=")
        preset, url = preset.strip(), url.strip()
        if not sep or preset not in PRESETS or not url.startswith(("https://", "http://")):
            msg = "RULECAM_WEBHOOKS must look like slack=https://…,generic=https://…"
            raise WebhookError(msg)
        targets.append(WebhookTarget.model_validate({"preset": preset, "url": url}))
    return tuple(targets)


def canonical(value: object) -> bytes:
    """Compact, key-sorted UTF-8 JSON (what is hashed and sent)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def generic_payload(event: AlertEvent) -> dict[str, object]:
    """Rulecam's own JSON shape, without the id (which is the hash of this)."""
    return {
        "type": "rulecam.alert",
        "site": event.site,
        "event_id": event.event_id,
        "rule_id": event.rule_id,
        "role": event.role,
        "title": event.title,
        "body": event.body,
        "ts": event.ts.isoformat(),
        "link": event.link,
    }


def event_hash(event: AlertEvent) -> str:
    """The idempotency key: SHA-256 of the canonical generic payload."""
    return hashlib.sha256(canonical(generic_payload(event))).hexdigest()


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _facts(event: AlertEvent) -> list[tuple[str, str]]:
    facts = [("Site", event.site), ("Role", event.role)]
    facts += [("Rule", event.rule_id)] if event.rule_id else []
    facts += [("Event", event.event_id)] if event.event_id else []
    return facts


def slack_payload(event: AlertEvent) -> dict[str, object]:
    """A Slack incoming-webhook message: fallback `text` and Block Kit blocks."""
    context = " · ".join(f"{name}: {_slack_escape(value)}" for name, value in _facts(event))
    if event.link:
        context += f" · <{event.link}|Incident report>"
    return {
        "text": f"{event.title}\n{event.body}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": event.title[:SLACK_HEADER_CHARS]},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _slack_escape(event.body)[:SLACK_SECTION_CHARS]},
            },
            {"type": "context", "elements": [{"type": "mrkdwn", "text": context}]},
        ],
    }


def teams_payload(event: AlertEvent) -> dict[str, object]:
    """An Adaptive Card message for a Teams Workflows webhook trigger."""
    body: list[dict[str, object]] = [
        {
            "type": "TextBlock",
            "text": event.title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        },
        {"type": "TextBlock", "text": event.body, "wrap": True},
        {
            "type": "FactSet",
            "facts": [{"title": name, "value": value} for name, value in _facts(event)],
        },
    ]
    card: dict[str, object] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if event.link:
        card["actions"] = [
            {"type": "Action.OpenUrl", "title": "Incident report", "url": event.link}
        ]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }


def payload_for(preset: Preset, event: AlertEvent) -> dict[str, object]:
    """The request body for one preset."""
    if preset == "slack":
        return slack_payload(event)
    if preset == "teams":
        return teams_payload(event)
    return {"id": event_hash(event), **generic_payload(event)}


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """`v1=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>`."""
    message = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_VERSION}={digest}"


def verify_signature(
    secret: str,
    *,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: datetime,
    tolerance_s: int = TOLERANCE_S,
) -> bool:
    """Check a request's signature and freshness, as a receiver should (constant time).

    Args:
        secret: The shared `RULECAM_WEBHOOK_SECRET`.
        timestamp: The `X-Rulecam-Timestamp` header.
        signature: The `X-Rulecam-Signature` header.
        body: The raw request body.
        now: The receiver's current time.
        tolerance_s: How old a request may be.

    Returns:
        Whether the request is genuine and recent.
    """
    if not timestamp or not signature or not timestamp.isdigit():
        return False
    if abs(now.timestamp() - int(timestamp)) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, int(timestamp), body), signature)


@dataclass
class _Delivery:
    target: WebhookTarget
    body: bytes
    key: str
    attempts: int = 0
    next_at: datetime | None = None


class HttpxPoster:
    """The real `Poster`: one short-timeout `httpx` POST, no redirects followed."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        """Create the poster.

        Args:
            client: A preconfigured client (tests pass one with `httpx.MockTransport`).
        """
        self._client = client or httpx.Client(timeout=POST_TIMEOUT_S, follow_redirects=False)

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str]) -> int:
        """POST `body` to `url` and return the status code (raises `httpx.HTTPError`)."""
        return self._client.post(url, content=body, headers=dict(headers)).status_code


class WebhookDispatcher:
    """A bounded, thread-safe retry queue of signed webhook deliveries."""

    def __init__(
        self,
        targets: Sequence[WebhookTarget],
        *,
        post: Poster,
        clock: Clock,
        secret: str | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        base_delay_s: float = BASE_DELAY_S,
        capacity: int = QUEUE_CAPACITY,
    ) -> None:
        """Create the dispatcher.

        Args:
            targets: Where every alert goes.
            post: Sends one request (`HttpxPoster`; a fake in tests).
            clock: Time source for signatures and backoff.
            secret: The HMAC secret; without it requests are unsigned.
            max_attempts: Attempts per delivery, the first included.
            base_delay_s: Delay before the first retry; doubles each time (capped at 5 min).
            capacity: Queue size; the oldest delivery is dropped when it is full.

        Raises:
            WebhookError: If a `generic` target has no secret to sign with.
        """
        if secret is None and any(t.preset == "generic" for t in targets):
            msg = "a generic webhook needs RULECAM_WEBHOOK_SECRET to sign its requests"
            raise WebhookError(msg)
        self._targets = tuple(targets)
        self._post = post
        self._clock = clock
        self._secret = secret
        self._max_attempts = max_attempts
        self._base_delay_s = base_delay_s
        self._queue: deque[_Delivery] = deque()
        self._capacity = capacity
        self._seen = SeenIds()
        self._lock = threading.Lock()
        self._wake = threading.Event()

    @property
    def presets(self) -> tuple[str, ...]:
        """The configured presets (no URLs)."""
        return tuple(t.preset for t in self._targets)

    def pending(self) -> int:
        """How many deliveries are waiting."""
        with self._lock:
            return len(self._queue)

    def enqueue(self, event: AlertEvent) -> str:
        """Queue one delivery per target (never blocks on the network).

        Returns:
            The event hash (the idempotency key).
        """
        key = event_hash(event)
        with self._lock:
            for target in self._targets:
                if not self._seen.add(f"{key}:{target.url}"):
                    continue
                if len(self._queue) >= self._capacity:
                    dropped = self._queue.popleft()
                    _log.warning("webhook_dropped", preset=dropped.target.preset, key=dropped.key)
                body = canonical(payload_for(target.preset, event))
                self._queue.append(_Delivery(target=target, body=body, key=key))
        self._wake.set()
        return key

    def headers(self, delivery: _Delivery) -> dict[str, str]:
        """The request headers, signed now when a secret is set."""
        headers = {
            "Content-Type": "application/json",
            IDEMPOTENCY_HEADER: delivery.key,
            EVENT_HEADER: "alert",
        }
        if self._secret is not None:
            timestamp = int(self._clock.now().timestamp())
            headers[TIMESTAMP_HEADER] = str(timestamp)
            headers[SIGNATURE_HEADER] = sign(self._secret, timestamp, delivery.body)
        return headers

    def deliver_due(self) -> int:
        """Send every delivery that is due once; reschedule or drop the failures.

        Returns:
            How many were delivered.
        """
        now = self._clock.now()
        with self._lock:
            due = [d for d in self._queue if d.next_at is None or d.next_at <= now]
            for delivery in due:
                self._queue.remove(delivery)
        delivered = 0
        retry: list[_Delivery] = []
        for delivery in due:
            outcome = self._attempt(delivery)
            if outcome == "delivered":
                delivered += 1
            elif outcome == "retry":
                retry.append(delivery)
        with self._lock:
            self._queue.extend(retry)
        return delivered

    def poll(self) -> int:
        """Wait up to a second for new work, then deliver what is due (an API poller)."""
        self._wake.wait(WAIT_S)
        self._wake.clear()
        return self.deliver_due()

    def _attempt(self, delivery: _Delivery) -> Literal["delivered", "retry", "failed"]:
        delivery.attempts += 1
        preset = delivery.target.preset
        try:
            status: int | None = self._post(
                delivery.target.url, delivery.body, self.headers(delivery)
            )
        except (httpx.HTTPError, OSError) as exc:
            status = None
            _log.info("webhook_network_error", preset=preset, error=type(exc).__name__)
        if status is not None and HTTP_OK_MIN <= status <= HTTP_OK_MAX:
            _log.info(
                "webhook_delivered", preset=preset, key=delivery.key, attempts=delivery.attempts
            )
            return "delivered"
        retryable = status is None or status in RETRYABLE or status >= HTTP_SERVER_ERROR_MIN
        if not retryable or delivery.attempts >= self._max_attempts:
            _log.warning(
                "webhook_failed",
                preset=preset,
                key=delivery.key,
                status=status,
                attempts=delivery.attempts,
            )
            return "failed"
        delay = min(MAX_DELAY_S, self._base_delay_s * 2 ** (delivery.attempts - 1))
        delivery.next_at = self._clock.now() + timedelta(seconds=delay)
        return "retry"


class WebhookNotifier:
    """`Notifier`: the wrapped notifier first, then one queued webhook delivery per target."""

    def __init__(
        self,
        *,
        inner: Notifier,
        dispatcher: WebhookDispatcher,
        clock: Clock,
        site: str,
        link_base: str | None = None,
    ) -> None:
        """Create the notifier.

        Args:
            inner: The notifier that stores and sends the alert.
            dispatcher: The delivery queue.
            clock: Time source for the payload's `ts`.
            site: The site name in payloads.
            link_base: The web UI's origin; payloads then link the incident report.
        """
        self._inner = inner
        self._dispatcher = dispatcher
        self._clock = clock
        self._site = site
        self._link_base = link_base.rstrip("/") if link_base else None

    def send(
        self,
        role: str,
        title: str,
        body: str,
        *,
        clip_path: str | None = None,
        buttons: tuple[str, ...] = (),
    ) -> str:
        """Send through the inner notifier, then queue the webhooks (clips are never sent).

        Returns:
            The inner notifier's alert id.
        """
        alert_id = self._inner.send(role, title, body, clip_path=clip_path, buttons=buttons)
        event_id = alert_event.get()
        link = f"{self._link_base}/incidents/{event_id}" if self._link_base and event_id else None
        self._dispatcher.enqueue(
            AlertEvent(
                site=self._site,
                event_id=event_id,
                rule_id=alert_rule.get(),
                role=role,
                title=title,
                body=body,
                ts=self._clock.now(),
                link=link,
            )
        )
        return alert_id
