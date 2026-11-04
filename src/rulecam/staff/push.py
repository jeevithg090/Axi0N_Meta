"""Web Push: alerts reach a role's phones even when the staff app is closed.

The standard needs three things, all here:

- **VAPID keys** (RFC 8292). An EC P-256 key pair is generated on first start into
  `<data>/staff/vapid.pem` (mode 600, never committed) and reused after that. The public key is
  the phone's `applicationServerKey`.
- **Subscriptions per role.** A phone that allowed notifications subscribes with its browser's
  push service and posts the subscription with its role. They are kept in `<data>/staff/push.json`.
  A subscription is a push-service URL and two keys: no name, no account, no phone number. Only
  HTTPS endpoints on the known browser push services are accepted, so the API never posts to an
  address a client made up.
- **Encrypted delivery** (RFC 8291, `aes128gcm`). Each message is encrypted for the one
  subscription with an ephemeral ECDH key, then posted with a VAPID JWT (ES256). A `404` or
  `410` from the push service means the subscription is gone, and it is dropped.

`PushRelay.tick` (on the API's one-second ticker) picks up every new alert the site's
`LanNotifier` sent, which covers rule alerts, reviews, crowd warnings and escalations, and pushes
it to the subscriptions of the alert's role. Sending happens on the relay's own threads, so a
slow push service never delays the escalation timers. The phone's service worker shows the
notification with the verdict and an *Acknowledge* action that opens the item.

Uses `cryptography` (Apache-2.0 / BSD), which python-kasa already brings in; no web-push
library is needed.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import ConfigDict, Field, ValidationError

from rulecam.act.notify import Alert
from rulecam.contracts import Frozen
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.staff.models import ITEM_ID_PATTERN, Role, StaffItem

_log = get_logger(__name__)

RECORD_SIZE: Final = 4096
TTL_S: Final = 3600
JWT_HOURS: Final = 12
MAX_PAYLOAD: Final = 3000  # bytes of JSON; push services take about 4 KB after encryption
SEND_TIMEOUT_S: Final = 10.0
GONE: Final = frozenset({404, 410})
# The browsers' push services (Chrome/Edge via FCM, Firefox, Safari, Windows).
PUSH_HOSTS: Final = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)
# Chrome builds without FCM keys (Chromium, Chrome for Testing) get `jmt<N>.google.com` endpoints.
LEGACY_GCM_HOST: Final = re.compile(r"^jmt\d+\.google\.com$")


class PushError(RulecamError):
    """A subscription that cannot be used (bad keys, an unknown push service)."""


def b64url(data: bytes) -> str:
    """Base64url without padding (the Web Push wire format)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Decode base64url with or without padding."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SubscriptionKeys(Frozen):
    """The browser's keys for one subscription."""

    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=64)


class Subscription(Frozen):
    """A browser push subscription (`PushSubscription.toJSON()`)."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    endpoint: str = Field(min_length=10, max_length=2000)
    expiration_time: float | None = Field(default=None, alias="expirationTime")
    keys: SubscriptionKeys


class SubscribeRequest(Frozen):
    """`POST /staff/push/subscribe`: this phone's subscription and its role."""

    role: Role
    lang: str = Field(default="en", pattern=r"^[a-z]{2}$")
    subscription: Subscription


class Subscriber(Frozen):
    """A stored subscription: the role it receives alerts for, never a person."""

    role: str
    lang: str
    subscription: Subscription
    since: datetime


class PushMessage(Frozen):
    """What the service worker gets (and shows)."""

    item_id: str = Field(pattern=ITEM_ID_PATTERN)
    title: str
    body: str
    verdict: str = ""
    severity: str = ""
    role: str
    url: str
    ts: datetime


def check_endpoint(endpoint: str) -> None:
    """Accept only HTTPS endpoints on a known browser push service.

    Raises:
        PushError: For anything else.
    """
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    known = any(host == h or host.endswith(f".{h}") for h in PUSH_HOSTS)
    known = known or LEGACY_GCM_HOST.match(host) is not None
    if parts.scheme != "https" or not known:
        msg = f"not a browser push service: {host or endpoint[:40]}"
        raise PushError(msg)


@dataclass(frozen=True)
class Vapid:
    """The server's VAPID key pair."""

    private_key: ec.EllipticCurvePrivateKey
    subject: str

    @property
    def public_key(self) -> str:
        """The public key as the browser's `applicationServerKey` (base64url, uncompressed)."""
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return b64url(raw)

    @classmethod
    def load_or_create(cls, path: Path, subject: str) -> Vapid:
        """Load the key from `path`, or generate one there (mode 600) on first start."""
        if path.is_file():
            loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(loaded, ec.EllipticCurvePrivateKey):
                msg = f"{path.name} is not an EC key"
                raise PushError(msg)
            return cls(loaded, subject)
        key = ec.generate_private_key(ec.SECP256R1())
        path.parent.mkdir(parents=True, exist_ok=True)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(pem)
        _log.info("vapid_key_created", path=str(path))
        return cls(key, subject)

    def authorization(self, endpoint: str, now: float) -> str:
        """The `Authorization: vapid t=<JWT>, k=<public key>` header for one push service."""
        parts = urlsplit(endpoint)
        header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        claims = {
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int(now) + JWT_HOURS * 3600,
            "sub": self.subject,
        }
        signing_input = f"{header}.{b64url(json.dumps(claims).encode())}"
        der = self.private_key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return f"vapid t={signing_input}.{b64url(signature)}, k={self.public_key}"


def _hkdf(salt: bytes, info: bytes, length: int, secret: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(secret)


def encrypt(payload: bytes, keys: SubscriptionKeys, *, salt: bytes | None = None) -> bytes:
    """Encrypt one message for one subscription (RFC 8291, `aes128gcm`, a single record).

    Args:
        payload: The message.
        keys: The subscription's `p256dh` (its public key) and `auth` secret.
        salt: 16 random bytes (tests pass their own).

    Returns:
        The request body: salt, record size, the sender's public key, then the ciphertext.

    Raises:
        PushError: When the subscription's keys are malformed.
    """
    try:
        ua_public = b64url_decode(keys.p256dh)
        auth_secret = b64url_decode(keys.auth)
        ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    except ValueError as exc:
        msg = "malformed subscription keys"
        raise PushError(msg) from exc
    sender = ec.generate_private_key(ec.SECP256R1())
    as_public = sender.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    shared = sender.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, b"WebPush: info\x00" + ua_public + as_public, 32, shared)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


class Poster(Protocol):
    """Posts one encrypted message (`httpx.post`; a fake in tests)."""

    def __call__(self, url: str, *, content: bytes, headers: Mapping[str, str]) -> int:
        """Return the HTTP status."""
        ...


def httpx_poster(url: str, *, content: bytes, headers: Mapping[str, str]) -> int:
    """POST with httpx and return the status."""
    return httpx.post(
        url, content=content, headers=dict(headers), timeout=SEND_TIMEOUT_S
    ).status_code


class SubscriberStore:
    """Subscriptions by endpoint, kept in one JSON file."""

    def __init__(self, path: Path) -> None:
        """Open (or start) the store at `path`."""
        self._path = path
        self._lock = threading.Lock()
        self._subs: dict[str, Subscriber] = {}
        if path.is_file():
            try:
                rows = json.loads(path.read_text("utf-8"))
                for row in rows if isinstance(rows, list) else []:
                    sub = Subscriber.model_validate(row)
                    self._subs[sub.subscription.endpoint] = sub
            except (ValueError, ValidationError):
                _log.warning("push_store_unreadable", path=str(path))

    def put(self, sub: Subscriber) -> None:
        """Add or update (a phone that changed role keeps one subscription)."""
        with self._lock:
            self._subs[sub.subscription.endpoint] = sub
            self._save()

    def drop(self, endpoint: str) -> bool:
        """Remove a subscription; True if it was there."""
        with self._lock:
            gone = self._subs.pop(endpoint, None) is not None
            if gone:
                self._save()
            return gone

    def for_role(self, role: str) -> list[Subscriber]:
        """Every subscription of a role."""
        with self._lock:
            return [s for s in self._subs.values() if s.role == role]

    def roles(self) -> dict[str, int]:
        """How many phones each role has subscribed."""
        with self._lock:
            counts: dict[str, int] = {}
            for sub in self._subs.values():
                counts[sub.role] = counts.get(sub.role, 0) + 1
            return counts

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        rows = [s.model_dump(mode="json") for s in self._subs.values()]
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows), encoding="utf-8")
        tmp.replace(self._path)


def message_for(item: StaffItem | None, alert: Alert) -> PushMessage:
    """The notification for one alert, from its feed item when there is one."""
    verdict = ""
    if item is not None and item.verdict is not None:
        v = item.verdict
        score = f" {round(v.confidence * 100)}%" if v.confidence is not None else ""
        verdict = f"{v.model}: {v.verdict}{score}"
    where = (item.camera_name or item.camera_id) if item is not None else None
    item_id = item.item_id if item is not None else alert.event_id or alert.alert_id
    body = " · ".join(x for x in (verdict, where, alert.body.splitlines()[0][:160]) if x)
    return PushMessage(
        item_id=item_id,
        title=alert.title[:120],
        body=body[:300],
        verdict=verdict,
        severity=item.severity if item is not None else "",
        role=alert.role,
        url=f"/staff?item={item_id}",
        ts=alert.ts,
    )


class PushRelay:
    """Sends Web Push for new alerts to the alert's role."""

    def __init__(
        self,
        *,
        vapid: Vapid,
        store: SubscriberStore,
        alerts: Callable[[], Sequence[Alert]],
        item: Callable[[str], StaffItem | None],
        poster: Poster = httpx_poster,
        now: Callable[[], float] = time.time,
        workers: int = 4,
    ) -> None:
        """Create the relay.

        Args:
            vapid: The server's VAPID keys.
            store: The subscriptions.
            alerts: The notifier's alerts (`LanNotifier.list`).
            item: The feed item of an alert (verdict, camera) by its event id.
            poster: Posts to the push service.
            now: Wall clock (VAPID token expiry).
            workers: Sending threads.
        """
        self._vapid = vapid
        self._store = store
        self._alerts = alerts
        self._item = item
        self._poster = poster
        self._now = now
        self._seen: set[str] | None = None
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="staff-push")
        self._lock = threading.Lock()
        self.sent = 0
        self.failed = 0

    @property
    def public_key(self) -> str:
        """The VAPID public key for the phone's `applicationServerKey`."""
        return self._vapid.public_key

    @property
    def store(self) -> SubscriberStore:
        """The subscriptions."""
        return self._store

    def tick(self) -> int:
        """Push every alert sent since the last tick (alerts from before start are not pushed).

        Returns:
            How many alerts were queued for pushing.
        """
        alerts = self._alerts()
        with self._lock:
            if self._seen is None:
                self._seen = {a.alert_id for a in alerts}
                return 0
            fresh = [a for a in alerts if a.alert_id not in self._seen]
            self._seen.update(a.alert_id for a in fresh)
        for alert in sorted(fresh, key=lambda a: a.ts):
            subs = self._store.for_role(alert.role)
            if not subs:
                continue
            key = alert.event_id or alert.alert_id
            message = message_for(self._item(key), alert)
            for sub in subs:
                self._pool.submit(self.send, sub, message)
        return len(fresh)

    def send(self, sub: Subscriber, message: PushMessage) -> int:
        """Encrypt and post one message; drop the subscription when the service says it is gone.

        Returns:
            The push service's HTTP status (0 when the request failed).
        """
        endpoint = sub.subscription.endpoint
        body = message.model_dump_json().encode()[:MAX_PAYLOAD]
        headers = {
            "Authorization": self._vapid.authorization(endpoint, self._now()),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(TTL_S),
            "Urgency": "high",
        }
        try:
            status = self._poster(
                endpoint, content=encrypt(body, sub.subscription.keys), headers=headers
            )
        except (httpx.HTTPError, PushError) as exc:
            self.failed += 1
            _log.warning("staff_push_failed", role=sub.role, error=str(exc)[:200])
            return 0
        if status in GONE:
            self._store.drop(endpoint)
        if 200 <= status < 300:  # noqa: PLR2004 - HTTP success range
            self.sent += 1
        else:
            self.failed += 1
        _log.info("staff_push", role=sub.role, item_id=message.item_id, status=status)
        return status
