"""Tests for worker transparency and tap dispute API (task P2-11, MP §3.5, UC2)."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.api.worker import DisputeRequest, WorkerStore, worker_router
from rulecam.bus import Bus
from tests.fakes import FakeLLM


@pytest.fixture
def test_client() -> TestClient:
    app = FastAPI()
    store = WorkerStore()
    spark = FakeLLM()
    bus = Bus()
    router = worker_router(store=store, spark=spark, bus=bus)
    app.include_router(router)
    return TestClient(app)


def test_worker_page_payload_never_contains_track_ids_or_clip_paths(
    test_client: TestClient,
) -> None:
    """Guardrail test: payload must never leak track_id or video clip paths."""
    resp = test_client.get("/worker/dock_site/cam1?lang=en")
    assert resp.status_code == 200
    data = resp.json()

    # Verify basic response fields
    assert data["site"] == "dock_site"
    assert data["camera"] == "cam1"
    assert "faces" in data["never_list"]
    assert "names" in data["never_list"]
    assert len(data["rules"]) >= 2

    # String scan across entire response JSON
    raw_json = json.dumps(data).lower()
    assert "track_id" not in raw_json
    assert "tracker_id" not in raw_json
    assert ".mp4" not in raw_json
    assert "/var/videos/" not in raw_json
    assert "data/chunks/" not in raw_json


def test_spanish_back_translation_and_counts(test_client: TestClient) -> None:
    """Worker page supports Spanish back-translations and reports per-rule counts."""
    resp = test_client.get("/worker/dock_site/cam1?lang=es")
    assert resp.status_code == 200
    data = resp.json()

    assert data["lang"] == "es"
    strap_rule = next(r for r in data["rules"] if r["rule_id"] == "strap-harness-check")
    assert (
        "arnés" in strap_rule["plain_language"].lower()
        or "correa" in strap_rule["plain_language"].lower()
    )

    # Public counts structure
    counts = data["public_counts"]["strap-harness-check"]
    assert counts["alerts"] >= counts["disputes"]
    assert counts["disputes"] >= counts["upheld"]


def test_dispute_flow_uc2_spanish_strap_alert_upheld_updates_counts(
    test_client: TestClient,
) -> None:
    """UC2 Acceptance Criterion: A Spanish tap dispute on strap alert is upheld."""
    # Read initial counts
    resp_init = test_client.get("/worker/dock_site/cam1?lang=es")
    initial_counts = resp_init.json()["public_counts"]["strap-harness-check"]
    init_disputes = initial_counts["disputes"]
    init_upheld = initial_counts["upheld"]

    # Submit Spanish tap dispute on strap event
    payload = {
        "event_code": "ev-strap-01",
        "reason_choice": "backpack_strap_not_harness",
        "reason_text": "Es la correa de mi mochila, no el arnés suelto.",
    }
    resp = test_client.post(
        "/worker/dispute",
        json=payload,
        headers={"User-Agent": "Mobile-Worker-1"},
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["outcome"] == "upheld"
    assert "backpack strap" in res_data["reviewer_reasoning"].lower()

    # Verify counts increased
    updated_counts = res_data["updated_counts"]
    assert updated_counts["disputes"] == init_disputes + 1
    assert updated_counts["upheld"] == init_upheld + 1


def test_dispute_payload_schema_has_no_identity_fields() -> None:
    """Dispute payload schema must strictly prohibit personal identity fields."""
    fields = set(DisputeRequest.model_fields.keys())
    assert fields == {"event_code", "reason_choice", "reason_text"}
    assert "name" not in fields
    assert "email" not in fields
    assert "ip" not in fields
    assert "user_id" not in fields
    assert "worker_id" not in fields


def test_rate_limiting_with_daily_salted_hash(test_client: TestClient) -> None:
    """Rate limit enforces threshold using salted daily hash without IP logging."""
    client_header = {"User-Agent": "RateLimitTestDevice-ABC"}
    payload = {
        "event_code": "ev-forklift-02",
        "reason_choice": "distance_miscalculated",
        "reason_text": "Distance was over 2 metres from forklift",
    }

    # Submit up to limit (limit is 10)
    for _ in range(10):
        resp = test_client.post("/worker/dispute", json=payload, headers=client_header)
        assert resp.status_code == 200

    # 11th request must be rejected with 429
    resp_overflow = test_client.post("/worker/dispute", json=payload, headers=client_header)
    assert resp_overflow.status_code == 429
    assert "Rate limit exceeded" in resp_overflow.json()["detail"]


def test_transparency_poster_renders_html_with_qr_code(test_client: TestClient) -> None:
    """Printable A4 transparency poster renders valid HTML with QR link."""
    resp = test_client.get("/worker/dock_site/cam1/poster")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "A4" in html
    assert "Safety Camera in Operation" in html
    assert "No facial recognition" in html
    assert "https://rulecam.site/worker/dock_site/cam1" in html
