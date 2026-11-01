"""Tests for the `/advice` routes (P3-10)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.advisor.api import AdviceStore, PlanFactory, advice_router
from rulecam.advisor.recompute import Recomputer
from rulecam.advisor.seal import SealBox
from rulecam.bus import Bus, Topic
from rulecam.evidence.chain import EventChain
from tests.advisor.card_helpers import (
    TURN_SQL,
    WAIT_SQL,
    FakeSqlWriter,
    make_card,
    make_db,
    make_plan,
    numbers,
)
from tests.fakes import FakeClock

OWNER: dict[str, object] = {"base_url": "http://127.0.0.1", "client": ("127.0.0.1", 5000)}
TUNNEL: dict[str, object] = {"base_url": "https://demo.tunnel.test", "client": ("203.0.113.7", 1)}


def build(
    tmp_path: Path, factory: PlanFactory | None
) -> tuple[FastAPI, AdviceStore, EventChain, list[object]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = AdviceStore()
    writer = FakeSqlWriter(
        {"dock_wait_for_forklift_min": WAIT_SQL, "dock_turnaround_min": TURN_SQL}
    )
    report = Recomputer(writer, make_db(tmp_path / "a.duckdb")).check_card(make_card(), numbers())
    store.add(make_card(), report)
    clock = FakeClock()
    chain = EventChain(tmp_path / "chain", "site", clock=clock)
    bus = Bus()
    published: list[object] = []
    bus.publish = lambda topic, payload: published.append((topic, payload))  # type: ignore[method-assign]
    app = FastAPI()
    app.include_router(advice_router(store, chain, bus, clock, plan_factory=factory))
    return app, store, chain, published


def test_cards_carry_recompute_results(tmp_path: Path) -> None:
    app, _, _, _ = build(tmp_path, None)
    with TestClient(app, **OWNER) as client:  # type: ignore[arg-type]
        cards = client.get("/advice").json()
        assert len(cards) == 1
        card = cards[0]
        assert card["recommendation"]["numbers_total"] == 2
        assert card["recommendation"]["numbers_reproduced"] == 2
        assert card["flagged"] is False
        assert card["hidden"] == []
        assert card["checks"][0]["sql"] == WAIT_SQL
        assert client.get("/advice/rec-0007").json()["recommendation"]["id"] == "rec-0007"
        assert client.get("/advice/nope").status_code == 404
        assert client.get("/advice/bad%20id").status_code == 422


def test_accept_needs_a_planner_and_the_owner(tmp_path: Path) -> None:
    app, _, _, _ = build(tmp_path, None)
    with TestClient(app, **OWNER) as client:  # type: ignore[arg-type]
        assert client.post("/advice/rec-0007/accept").status_code == 503
    app, _, _, _ = build(tmp_path / "b", lambda rec: make_plan(rec.id))
    with TestClient(app, **TUNNEL) as remote:  # type: ignore[arg-type]
        assert remote.post("/advice/rec-0007/accept").status_code == 403


def test_accept_preregisters_and_publishes(tmp_path: Path) -> None:
    app, store, _chain, published = build(tmp_path, lambda rec: make_plan(rec.id))
    with TestClient(app, **OWNER) as client:  # type: ignore[arg-type]
        reply = client.post("/advice/rec-0007/accept").json()
        assert reply["recommendation"]["status"] == "measuring"
        assert reply["preregistered_seq"] == 1
        assert len(reply["measurement_plan"]["preregistration_hash"]) == 64
        assert client.get("/advice/rec-0007").json()["preregistered_seq"] == 1
        assert client.post("/advice/rec-0007/accept").status_code == 409
    assert published[0][0] == Topic.ADVICE_NEW  # type: ignore[index]
    assert store.get("rec-0007").recommendation.status == "measuring"


def test_seals_route(tmp_path: Path) -> None:
    app, _, chain, _ = build(tmp_path, None)
    with TestClient(app, **OWNER) as client:  # type: ignore[arg-type]
        assert client.get("/advice/seals").json() == []
        box = SealBox(chain, tmp_path / "seals")
        seal_id, _ = box.seal("both forklifts at 13:00")
        box.reveal(seal_id)
        (status,) = client.get("/advice/seals").json()
        assert status["matches"] is True


def test_store_is_bounded_and_newest_first() -> None:
    store = AdviceStore(max_cards=2)
    from rulecam.advisor.recompute import RecomputeReport  # noqa: PLC0415

    for i in range(3):
        card = make_card().model_copy(update={"id": f"rec-{i}"})
        store.add(card, RecomputeReport(recommendation_id=card.id, checks=()))
    assert [c.recommendation.id for c in store.all()] == ["rec-2", "rec-1"]


def test_verdict_attaches_to_a_card(tmp_path: Path) -> None:
    from rulecam.contracts import Verdict  # noqa: PLC0415

    _, store, _, _ = build(tmp_path, None)
    verdict = Verdict(
        plan_id="mp-0007", label="worked", effect=-9, ci=(-14, -4), p_value=0.031, pairs_done=6
    )
    card = store.set_verdict("rec-0007", verdict)
    assert card.verdict == verdict
    assert store.get("rec-0007").checks[0].unit == "min"
