"""P1-09 token lifecycle: create, use, reuse rejected, expiry (FakeClock), and the rate limiter."""

from __future__ import annotations

import base64
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.capture.pair_phone import PairingRegistry, PairPhone, RateLimiter, build_router
from rulecam.capture.pair_phone.errors import (
    SessionInvalidError,
    TokenExpiredError,
    TokenUnknownError,
    TokenUsedError,
)
from rulecam.capture.pair_phone.tokens import TOKEN_TTL
from rulecam.config import Settings
from tests.capture.pair_phone.conftest import (
    OWNER_ADDR,
    TUNNEL,
    FakeFrameSegmenter,
    RecordingBus,
)
from tests.fakes import EPOCH, FakeClock

# --- registry ---------------------------------------------------------------------------------


def test_issue_gives_unguessable_tokens_that_expire_after_ten_minutes() -> None:
    registry = PairingRegistry(FakeClock())

    first, second = registry.issue(), registry.issue()

    assert first.token != second.token
    assert len(first.token) >= 22
    assert first.expires_at == EPOCH + timedelta(minutes=10)


def test_claim_pairs_one_phone_and_returns_a_session() -> None:
    registry = PairingRegistry(FakeClock())
    token = registry.issue().token

    camera, session = registry.claim(token, "Sink wall")

    assert camera.camera_id == "phone-1"
    assert camera.label == "Sink wall"
    assert registry.authenticate(token, session) == camera
    assert registry.cameras() == [camera]


def test_reusing_a_claimed_token_is_rejected() -> None:
    registry = PairingRegistry(FakeClock())
    token = registry.issue().token
    registry.claim(token, "first phone")

    with pytest.raises(TokenUsedError):
        registry.claim(token, "second phone")
    assert [c.camera_id for c in registry.cameras()] == ["phone-1"]


def test_unclaimed_token_expires_at_ten_minutes() -> None:
    clock = FakeClock()
    registry = PairingRegistry(clock)
    token = registry.issue().token

    clock.advance(TOKEN_TTL.total_seconds() - 1)
    registry.check_open(token)
    clock.advance(1)

    with pytest.raises(TokenExpiredError):
        registry.check_open(token)
    with pytest.raises(TokenExpiredError):
        registry.claim(token, "late phone")


def test_a_paired_phone_outlives_the_token_expiry() -> None:
    clock = FakeClock()
    registry = PairingRegistry(clock)
    token = registry.issue().token
    camera, session = registry.claim(token, "")
    clock.advance(3600)

    assert registry.authenticate(token, session) == camera
    assert camera.label == "phone-1"


def test_wrong_or_missing_session_is_rejected() -> None:
    registry = PairingRegistry(FakeClock())
    token = registry.issue().token
    with pytest.raises(SessionInvalidError):
        registry.authenticate(token, "anything")
    registry.claim(token, "cam")

    with pytest.raises(SessionInvalidError):
        registry.authenticate(token, "not-the-session")


def test_unknown_token_is_rejected_and_expired_tokens_are_forgotten() -> None:
    clock = FakeClock()
    registry = PairingRegistry(clock)
    stale = registry.issue().token
    with pytest.raises(TokenUnknownError):
        registry.check_open("never-issued-token-0000")

    clock.advance(TOKEN_TTL.total_seconds() + 1)
    registry.issue()

    with pytest.raises(TokenUnknownError):
        registry.claim(stale, "cam")


def test_phone_numbers_continue_from_first_phone() -> None:
    registry = PairingRegistry(FakeClock(), first_phone=5)
    ids = [registry.claim(registry.issue().token, "")[0].camera_id for _ in range(2)]
    assert ids == ["phone-5", "phone-6"]


# --- rate limiter -----------------------------------------------------------------------------


def test_rate_limiter_allows_one_request_per_second_per_key() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock)

    assert limiter.retry_after("a") == 0.0
    assert limiter.retry_after("a") == pytest.approx(1.0)
    assert limiter.retry_after("b") == 0.0
    clock.advance(0.4)
    assert limiter.retry_after("a") == pytest.approx(0.6)
    clock.advance(0.6)
    assert limiter.retry_after("a") == 0.0


# --- over HTTP --------------------------------------------------------------------------------


def test_token_lifecycle_over_http(
    owner_client: TestClient, phone_client: TestClient, clock: FakeClock
) -> None:
    created = owner_client.post("/pair/token")
    assert created.status_code == 201, created.text
    body = created.json()
    token = body["token"]
    assert body["url"] == f"{TUNNEL}/pair/{token}"
    assert base64.b64decode(body["qr_png_base64"]).startswith(b"\x89PNG\r\n\x1a\n")

    assert phone_client.get(f"/pair/{token}").status_code == 200
    first = phone_client.post(f"/pair/{token}/claim", json={"label": "Sink wall"})
    assert first.status_code == 201
    assert first.json()["camera_id"] == "phone-1"

    clock.advance(1.0)
    reuse = phone_client.post(f"/pair/{token}/claim", json={"label": "Intruder"})
    assert reuse.status_code == 409
    assert phone_client.get(f"/pair/{token}").status_code == 200  # the paired phone may reload


def test_expired_token_over_http(
    owner_client: TestClient, phone_client: TestClient, clock: FakeClock
) -> None:
    token = owner_client.post("/pair/token").json()["token"]
    clock.advance(TOKEN_TTL.total_seconds() + 1)

    assert phone_client.get(f"/pair/{token}").status_code == 410
    assert phone_client.post(f"/pair/{token}/claim", json={"label": "x"}).status_code == 410


def test_unknown_and_malformed_tokens_over_http(phone_client: TestClient) -> None:
    assert phone_client.get("/pair/AAAAAAAAAAAAAAAAAAAAAA").status_code == 404
    assert phone_client.get("/pair/short").status_code == 422
    assert phone_client.get("/pair/..%2F..%2Fetc%2Fpasswd").status_code in (404, 422)


def test_claim_is_rate_limited_per_token(service: PairPhone, phone_client: TestClient) -> None:
    token = service.registry.issue().token
    assert phone_client.post(f"/pair/{token}/claim", json={"label": "a"}).status_code == 201

    again = phone_client.post(f"/pair/{token}/claim", json={"label": "a"})

    assert again.status_code == 429
    assert again.headers["Retry-After"] == "1"


def test_claim_rejects_control_characters_and_long_labels(
    service: PairPhone, phone_client: TestClient
) -> None:
    token = service.registry.issue().token
    assert phone_client.post(f"/pair/{token}/claim", json={"label": "a\nb"}).status_code == 422
    assert phone_client.post(f"/pair/{token}/claim", json={"label": "x" * 41}).status_code == 422


def test_token_url_falls_back_to_the_request_without_a_tunnel(tmp_path: Path) -> None:
    service = PairPhone(
        bus=RecordingBus(),
        clock=FakeClock(),
        segmenter=FakeFrameSegmenter(),
        settings=Settings(_env_file=None, rulecam_data_dir=tmp_path),
    )
    app = FastAPI()
    app.include_router(build_router(service))

    with TestClient(app, base_url="http://localhost:8000", client=OWNER_ADDR) as owner:
        body = owner.post("/pair/token").json()

    assert body["url"] == f"http://localhost:8000/pair/{body['token']}"
