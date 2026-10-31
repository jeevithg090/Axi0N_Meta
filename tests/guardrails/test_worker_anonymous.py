"""Guardrail (rule R13, task P2-11): the public worker page and its disputes stay anonymous.

The page is reachable by anyone with the poster's QR link: it must never carry track ids, event
ids, strip or clip paths or exact times, and disputes must never store an address, a user agent
or any other identity. Anyone may add checks here; nobody may weaken them.
"""

from __future__ import annotations

from pathlib import Path

from rulecam.evidence.chain import read_chain
from rulecam.worker import DisputeRequest
from tests.worker.helpers import PAGE, SITE, STRIP, TRACK_SUBJECT, dispute, world_with_event


def test_page_payload_never_contains_track_ids_event_ids_or_clip_paths(tmp_path: Path) -> None:
    """Guardrail: the public payload carries nothing that re-identifies a person or a clip."""
    world, _ = world_with_event(tmp_path)
    raw = world.client.get(PAGE).text
    for leak in (TRACK_SUBJECT, "track_id", "ev-strap-1", "subject", *STRIP, ".mp4", "data/"):
        assert leak not in raw
    assert "11:53" not in raw  # only the 15-minute bucket, never the exact time


def test_dispute_storage_has_no_identity(tmp_path: Path) -> None:
    """Guardrail: no address, user agent or other identity reaches the chain."""
    world, code = world_with_event(tmp_path)
    headers = {"User-Agent": "Pixel-7-Juan", "X-Forwarded-For": "203.0.113.9"}
    world.client.post("/worker/dispute", json=dispute(code), headers=headers)
    text = world.chain_text()
    for leak in ("testclient", "Pixel-7-Juan", "203.0.113.9"):
        assert leak not in text
    disputes = [
        r
        for r in read_chain(next((tmp_path / "chain" / SITE).glob("*.jsonl")))
        if r.kind == "dispute"
    ]
    assert {tuple(sorted(r.payload)) for r in disputes} == {
        ("camera_id", "dispute", "dispute_id", "event_id", "rule_id")
    }


def test_dispute_schema_has_no_identity_fields() -> None:
    """Guardrail: the dispute payload accepts exactly the code and the reason."""
    assert set(DisputeRequest.model_json_schema()["properties"]) == {
        "event_code",
        "reason_choice",
        "reason_text",
    }
