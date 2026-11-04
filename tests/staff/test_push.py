"""Web Push: VAPID, RFC 8291 encryption (decrypted here as the browser would), the relay."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.staff.api import StaffConfig, staff_router
from rulecam.staff.push import (
    PushError,
    PushRelay,
    Subscriber,
    SubscriberStore,
    Subscription,
    SubscriptionKeys,
    Vapid,
    b64url,
    b64url_decode,
    check_endpoint,
    encrypt,
)
from rulecam.staff.speech import Speaker
from tests.staff.conftest import Rig

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


class Browser:
    """A push subscription's private side: decrypts what the server sends (RFC 8291)."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"
        public = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.keys = SubscriptionKeys(p256dh=b64url(public), auth=b64url(self.auth))

    def decrypt(self, body: bytes) -> bytes:
        salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
        as_public = body[21 : 21 + idlen]
        assert rs == 4096
        ua_public = b64url_decode(self.keys.p256dh)
        sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
        shared = self.key.exchange(ec.ECDH(), sender)

        def hkdf(salt_: bytes, info: bytes, n: int, secret: bytes) -> bytes:
            return HKDF(hashes.SHA256(), n, salt_, info).derive(secret)

        ikm = hkdf(self.auth, b"WebPush: info\x00" + ua_public + as_public, 32, shared)
        cek = hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
        nonce = hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen :], None)
        assert plain.endswith(b"\x02")
        return plain[:-1]


class FakePushService:
    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.posts: list[tuple[str, bytes, Mapping[str, str]]] = []

    def __call__(self, url: str, *, content: bytes, headers: Mapping[str, str]) -> int:
        self.posts.append((url, content, dict(headers)))
        return self.status


def test_encryption_round_trips_through_the_browser_side() -> None:
    browser = Browser()
    body = encrypt(b'{"title": "Alert"}', browser.keys)
    assert browser.decrypt(body) == b'{"title": "Alert"}'
    with pytest.raises(PushError):
        encrypt(b"x", SubscriptionKeys(p256dh="A" * 40, auth="B" * 16))


def test_vapid_key_is_made_once_private_and_signs_a_valid_jwt(tmp_path: Path) -> None:
    path = tmp_path / "staff" / "vapid.pem"
    vapid = Vapid.load_or_create(path, "mailto:ops@example.com")
    assert path.stat().st_mode & 0o777 == 0o600
    again = Vapid.load_or_create(path, "mailto:ops@example.com")
    assert again.public_key == vapid.public_key
    assert len(b64url_decode(vapid.public_key)) == 65
    header = vapid.authorization(ENDPOINT, now=1_800_000_000)
    token = header.split("t=", 1)[1].split(",", 1)[0]
    assert header.endswith(f"k={vapid.public_key}")
    head, claims, sig = token.split(".")
    payload = json.loads(b64url_decode(claims))
    assert payload == {
        "aud": "https://fcm.googleapis.com",
        "exp": 1_800_000_000 + 12 * 3600,
        "sub": "mailto:ops@example.com",
    }
    raw = b64url_decode(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public = vapid.private_key.public_key()
    public.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    with pytest.raises(InvalidSignature):
        public.verify(der, b"tampered", ec.ECDSA(hashes.SHA256()))


def test_only_browser_push_services_are_accepted() -> None:
    check_endpoint(ENDPOINT)
    check_endpoint("https://web.push.apple.com/QGuQ")
    check_endpoint("https://jmt17.google.com/fcm/send/dKNO")
    bad_ones = (
        "http://fcm.googleapis.com/x",
        "https://evil.example/fcm.googleapis.com",
        "https://mail.google.com/x",
        "x",
    )
    for bad in bad_ones:
        with pytest.raises(PushError):
            check_endpoint(bad)


def _relay(rig: Rig, service: FakePushService) -> tuple[PushRelay, Browser]:
    browser = Browser()
    store = SubscriberStore(rig.tmp / "staff" / "push.json")
    store.put(
        Subscriber(
            role="aisle_lead",
            lang="hi",
            subscription=Subscription(endpoint=ENDPOINT, keys=browser.keys),
            since=rig.clock.now(),
        )
    )
    relay = PushRelay(
        vapid=Vapid.load_or_create(rig.tmp / "staff" / "vapid.pem", "mailto:x@example.com"),
        store=store,
        alerts=rig.notifier.list,
        item=rig.feed.item,
        poster=service,
        workers=1,
    )
    return relay, browser


def _wait(service: FakePushService, n: int) -> None:
    deadline = time.monotonic() + 5
    while len(service.posts) < n and time.monotonic() < deadline:
        time.sleep(0.01)


def test_new_alerts_are_pushed_to_their_role_with_the_verdict(rig: Rig) -> None:
    rig.rule_alert("ev-0")
    service = FakePushService()
    relay, browser = _relay(rig, service)
    assert relay.tick() == 0  # alerts from before start are not pushed
    rig.rule_alert("ev-1", roles=("aisle_lead", "store_manager"))
    assert relay.tick() == 1
    _wait(service, 1)
    url, body, headers = service.posts[0]
    assert url == ENDPOINT
    assert headers["Content-Encoding"] == "aes128gcm"
    assert headers["Authorization"].startswith("vapid t=")
    message = json.loads(browser.decrypt(body))
    assert message["item_id"] == "ev-1"
    assert message["url"] == "/staff?item=ev-1"
    assert message["verdict"] == "muse-glimmer-30b: violation 91%"
    assert "Aisle 4" in message["body"]
    assert relay.tick() == 0  # nothing new
    rig.clock.advance(61)
    rig.escalation.tick()  # the store manager is told: nobody of theirs subscribed
    assert relay.tick() == 1
    assert len(service.posts) == 1


def test_a_gone_subscription_is_dropped(rig: Rig) -> None:
    service = FakePushService(status=410)
    relay, _ = _relay(rig, service)
    relay.tick()
    rig.rule_alert("ev-2")
    relay.tick()
    _wait(service, 1)
    deadline = time.monotonic() + 5
    while relay.store.roles() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert relay.store.roles() == {}
    assert SubscriberStore(rig.tmp / "staff" / "push.json").roles() == {}


def test_push_routes(rig: Rig) -> None:
    service = FakePushService()
    relay, browser = _relay(rig, service)
    app = FastAPI()
    app.include_router(
        staff_router(
            rig.feed,
            bus=rig.bus,
            clock=rig.clock,
            speaker=Speaker(spark=None, engines=[], audio_dir=rig.tmp / "audio"),
            config=StaffConfig(
                langs=("en",), dry_run=False, ask=False, listen=False, shelf=False, speech=()
            ),
            photos_dir=rig.tmp / "photos",
            push=relay,
        )
    )
    http = TestClient(app)
    key = http.get("/staff/push/key").json()
    assert key == {"public_key": relay.public_key, "roles": {"aisle_lead": 1}}
    sub = {"endpoint": ENDPOINT + "2", "expirationTime": None, "keys": browser.keys.model_dump()}
    made = http.post("/staff/push/subscribe", json={"role": "steward", "subscription": sub})
    assert made.json()["roles"] == {"aisle_lead": 1, "steward": 1}
    bad = {"endpoint": "https://example.com/push", "keys": browser.keys.model_dump()}
    refused = http.post("/staff/push/subscribe", json={"role": "steward", "subscription": bad})
    assert refused.status_code == 422
    assert http.post("/staff/push/test", json={"role": "steward"}).json() == {"statuses": [201]}
    assert json.loads(browser.decrypt(service.posts[-1][1]))["title"].endswith("test alert")
    gone = http.post("/staff/push/unsubscribe", json={"endpoint": ENDPOINT + "2"})
    assert gone.json() == {"removed": True}
    off = FastAPI()
    off.include_router(
        staff_router(
            rig.feed,
            bus=rig.bus,
            clock=rig.clock,
            speaker=Speaker(spark=None, engines=[], audio_dir=rig.tmp / "audio"),
            config=StaffConfig(
                langs=("en",), dry_run=False, ask=False, listen=False, shelf=False, speech=()
            ),
            photos_dir=rig.tmp / "photos",
        )
    )
    assert TestClient(off).get("/staff/push/key").status_code == 503
