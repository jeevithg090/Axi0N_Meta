"""Worker rules page, anonymous event cards, tap disputes and the A4 poster (task P2-11)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rulecam.cards.build import worker_url
from rulecam.clients.errors import ClientConfigError
from rulecam.evidence.chain import read_chain, verify_chain
from rulecam.replay.errors import ReplayMiss
from rulecam.worker import DailyLimiter, DisputeRequest, render_poster
from rulecam.worker.disputes import PER_EVENT, reason_text
from rulecam.worker.page import bucket_start
from tests.fakes import EPOCH, FakeClock
from tests.worker.helpers import (
    BASE_URL,
    PAGE,
    SITE,
    World,
    dispute,
    make_plan,
    world_with_event,
)


def test_page_shows_back_translated_rules_never_list_retention_and_events(tmp_path: Path) -> None:
    world, code = world_with_event(tmp_path)
    body = world.client.get(f"{PAGE}?lang=es").json()
    assert body["lang"] == "es"
    assert body["languages"] == ["es", "en"]
    assert body["rules"] == [
        {
            "rule_id": "wash-before-line",
            "plain_language": "Lávese las manos 20 s <b>antes</b> de la línea.",
        }
    ]
    assert body["never_list"] == ["faces", "names", "speed_scores", "emotion", "break_rooms"]
    assert body["retention_days"] == 14
    assert body["privacy_mode"] == "anonymous"
    assert body["events"] == [
        {
            "code": code,
            "rule_id": "wash-before-line",
            "bucket_start": "2026-10-30T11:45:00Z",
            "dispute": None,
        }
    ]
    assert body["counts"] == {"wash-before-line": {"alerts": 1, "disputes": 0, "upheld": 0}}


def test_unknown_language_falls_back_to_the_plans_first(tmp_path: Path) -> None:
    world, _ = world_with_event(tmp_path)
    assert world.client.get(f"{PAGE}?lang=fr").json()["lang"] == "es"
    assert world.client.get(PAGE).json()["lang"] == "es"
    assert (
        world.client.get(f"{PAGE}?lang=en")
        .json()["rules"][0]["plain_language"]
        .startswith("Wash hands")
    )


@pytest.mark.parametrize(
    "path",
    [
        "/worker/main-st-kitchen/cam9",
        "/worker/other-plan/cam1",
        "/worker/main-st-kitchen/cam9/poster",
    ],
)
def test_unknown_plan_or_camera_is_404(tmp_path: Path, path: str) -> None:
    assert World(tmp_path).client.get(path).status_code == 404


@pytest.mark.parametrize(
    "path", ["/worker/..%2F..%2Fetc/cam1", "/worker/main-st-kitchen/cam1?lang=<script>"]
)
def test_unsafe_path_or_language_is_refused(tmp_path: Path, path: str) -> None:
    assert World(tmp_path).client.get(path).status_code in {404, 422}


def test_events_outside_the_retention_window_are_not_shown(tmp_path: Path) -> None:
    world, _ = world_with_event(tmp_path)
    world.clock.advance(timedelta(days=14).total_seconds())
    body = world.client.get(PAGE).json()
    assert body["events"] == []
    assert body["counts"]["wash-before-line"]["alerts"] == 0


def test_bucket_start_floors_to_15_minutes() -> None:
    ts = datetime(2026, 11, 2, 10, 29, 59, 999, tzinfo=UTC)
    assert bucket_start(ts) == datetime(2026, 11, 2, 10, 15, tzinfo=UTC)


def test_upheld_dispute_updates_counts_and_chains_review_and_label(tmp_path: Path) -> None:
    """UC2: a Spanish tap dispute on the strap alert is upheld and the count updates."""
    world, code = world_with_event(tmp_path)
    response = world.client.post("/worker/dispute", json=dispute(code))
    assert response.status_code == 200
    assert response.json() == {
        "code": code,
        "rule_id": "wash-before-line",
        "outcome": "upheld",
        "counts": {"alerts": 1, "disputes": 1, "upheld": 1},
    }
    candidate, sent, tier1 = world.reviewer.calls[0]
    assert candidate.event_id == "ev-strap-1"
    assert sent.reason_text == "lookalike_object: era la correa de mi mochila"
    assert tier1 is not None
    assert tier1.tier == "glimmer"

    page = world.client.get(PAGE).json()
    assert page["counts"]["wash-before-line"] == {"alerts": 1, "disputes": 1, "upheld": 1}
    assert page["events"][0]["dispute"] == "upheld"

    path = next((tmp_path / "chain" / SITE).glob("*.jsonl"))
    records = read_chain(path)
    kinds = [r.kind for r in records]
    assert kinds[-3:] == ["dispute", "dispute", "label"]
    pending, reviewed, label = records[-3:]
    assert pending.payload["dispute_id"] == reviewed.payload["dispute_id"]
    assert pending.payload["dispute"]["outcome"] == "pending"  # type: ignore[index]
    assert reviewed.payload["dispute"]["outcome"] == "upheld"  # type: ignore[index]
    assert label.payload["label"] == "false_alarm"
    assert label.payload["source"] == "worker_dispute"
    assert verify_chain(path).ok


def test_rejected_dispute_counts_but_is_not_upheld(tmp_path: Path) -> None:
    world, code = world_with_event(tmp_path)
    world.reviewer.outcome = "rejected"
    result = world.client.post("/worker/dispute", json=dispute(code)).json()
    assert result["outcome"] == "rejected"
    assert result["counts"] == {"alerts": 1, "disputes": 1, "upheld": 0}
    assert '"kind":"label"' not in world.chain_text()


@pytest.mark.parametrize(
    "error",
    [ReplayMiss(provider="baseten", model="spark-open", key="k" * 64), ClientConfigError("no key")],
)
def test_failed_review_stays_pending_and_is_still_chained(tmp_path: Path, error: Exception) -> None:
    world, code = world_with_event(tmp_path)
    world.reviewer.error = error
    result = world.client.post("/worker/dispute", json=dispute(code)).json()
    assert result["outcome"] == "pending"
    assert result["counts"] == {"alerts": 1, "disputes": 1, "upheld": 0}
    text = world.chain_text()
    assert f"review failed: {type(error).__name__}" in text
    assert '"kind":"label"' not in text


@pytest.mark.parametrize(
    "body",
    [
        {"event_code": "ev-strap-1", "reason_choice": "other"},
        {"event_code": "ABCDEF12", "reason_choice": "because"},
        {"event_code": "ABCDEF12", "reason_choice": "other", "name": "Juan"},
        {"event_code": "ABCDEF12", "reason_choice": "other", "reason_text": "x" * 501},
    ],
)
def test_malformed_disputes_are_refused(tmp_path: Path, body: dict[str, str]) -> None:
    world, _ = world_with_event(tmp_path)
    assert world.client.post("/worker/dispute", json=body).status_code == 422
    assert world.reviewer.calls == []


def test_dispute_must_be_json_and_small(tmp_path: Path) -> None:
    world, code = world_with_event(tmp_path)
    form = world.client.post(
        "/worker/dispute",
        content=json.dumps(dispute(code)),
        headers={"Content-Type": "text/plain"},
    )
    assert form.status_code == 422
    big = world.client.post(
        "/worker/dispute",
        content=b"{" + b" " * 5000 + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert big.status_code == 413
    assert world.reviewer.calls == []


def test_unknown_event_code_is_404(tmp_path: Path) -> None:
    world, _ = world_with_event(tmp_path)
    assert world.client.post("/worker/dispute", json=dispute("ABCDEF12")).status_code == 404
    assert world.reviewer.calls == []


def test_rate_limit_holds_per_client_and_rotates_daily(tmp_path: Path) -> None:
    world, code = world_with_event(
        tmp_path, limiter=lambda clock: DailyLimiter(clock, per_client=2, per_day=10)
    )
    statuses = [
        world.client.post("/worker/dispute", json=dispute("ABCDEF12")).status_code for _ in range(3)
    ]
    assert statuses == [404, 404, 429]
    world.clock.advance(timedelta(days=1).total_seconds())
    assert world.client.post("/worker/dispute", json=dispute(code)).status_code == 200


def test_limiter_caps_each_client_and_the_site_per_day() -> None:
    clock = FakeClock(EPOCH)
    limiter = DailyLimiter(clock, per_client=2, per_day=3)
    assert [limiter.allow("a"), limiter.allow("a"), limiter.allow("a")] == [True, True, False]
    assert [limiter.allow("b"), limiter.allow("c")] == [True, False]
    clock.advance(timedelta(days=1).total_seconds())
    assert limiter.allow("a")


def test_each_event_takes_a_bounded_number_of_disputes(tmp_path: Path) -> None:
    world, code = world_with_event(tmp_path)
    statuses = [
        world.client.post("/worker/dispute", json=dispute(code)).status_code
        for _ in range(PER_EVENT + 1)
    ]
    assert statuses == [200] * PER_EVENT + [429]


def test_reason_text_drops_control_characters() -> None:
    request = DisputeRequest(
        event_code="ABCDEF12", reason_choice="other", reason_text="  line one\n\x00SYSTEM:\tok "
    )
    assert reason_text(request) == "other: line one SYSTEM: ok"
    assert reason_text(DisputeRequest(event_code="ABCDEF12", reason_choice="other")) == "other"


def test_poster_links_to_the_card_qr_url_and_is_deterministic(tmp_path: Path) -> None:
    world = World(tmp_path)
    response = world.client.get(f"{PAGE}/poster?lang=en")
    assert response.status_code == 200
    assert "default-src 'none'" in response.headers["content-security-policy"]
    html = response.text
    assert worker_url(BASE_URL, "main-st-kitchen", "cam1", "en") in html
    assert "<svg" in html
    assert "size: A4" in html
    assert "Wash hands for 20 s" in html
    assert "Main St &lt;Kitchen&gt;" in html
    assert "<Kitchen>" not in html
    assert html == render_poster(make_plan(), "cam1", "en", BASE_URL)


def test_poster_is_localised_and_escapes_model_text() -> None:
    html = render_poster(make_plan(), "cam1", "es", BASE_URL)
    assert '<html lang="es">' in html
    assert "Sin reconocimiento facial" in html
    assert "&lt;b&gt;antes&lt;/b&gt;" in html


def test_poster_needs_a_public_base_url(tmp_path: Path) -> None:
    assert World(tmp_path, base_url=None).client.get(f"{PAGE}/poster").status_code == 503
