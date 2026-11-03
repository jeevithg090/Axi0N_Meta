"""Tests for live site context (search-grounded Muse Spark): source checks, relink, routes."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from tests.fakes import FakeClock

from rulecam.clients.errors import ModelResponseError
from rulecam.clients.meta_hosted.grounding import Grounded, SearchSource, normal_url
from rulecam.context import (
    ContextDraft,
    ContextError,
    ContextItem,
    SiteContextService,
    SiteLocation,
    check,
    context_router,
    relink,
)

RECALL_PAGE = "https://alerts.food.gov.uk/news-alerts/alert/FSA-AA-50-2026-update-1"
FORECAST = "https://weather.metoffice.gov.uk/forecast/gcw2hzs1u"
SOURCES = (
    SearchSource(url=RECALL_PAGE, title="Walkers recall", snippet="batch 107543"),
    SearchSource(url="https://www.visitmanchester.com/event/x/1", title="Bonfire night"),
)


def item(title: str, url: str, kind: str = "recall") -> ContextItem:
    return ContextItem.model_validate(
        {"kind": kind, "title": title, "summary": f"{title} summary", "source_url": url}
    )


def found(items: list[ContextItem], cited: tuple[str, ...] = ()) -> Grounded[ContextDraft]:
    return Grounded(
        answer=ContextDraft(items=items),
        sources=SOURCES,
        cited=cited,
        searches=3,
        _index=frozenset(normal_url(s.url) for s in SOURCES),
    )


class FakeSpark:
    """`grounded` returns a queued `Grounded`; `structured` returns queued relink picks."""

    def __init__(self, grounded: Grounded[ContextDraft], picks: list[dict[str, Any]]) -> None:
        self._grounded = grounded
        self._picks: deque[dict[str, Any]] = deque(picks)
        self.grounded_calls: list[dict[str, Any]] = []
        self.structured_calls: list[dict[str, Any]] = []

    def grounded(self, **kwargs: Any) -> Grounded[ContextDraft]:
        self.grounded_calls.append(kwargs)
        return self._grounded

    def structured[M: BaseModel](self, *, schema: type[M], **kwargs: Any) -> M:
        self.structured_calls.append(kwargs)
        return schema.model_validate(self._picks.popleft())


def test_check_keeps_retrieved_sources_and_splits_the_rest() -> None:
    items = [
        item(
            "Walkers recall",
            "https://www.alerts.food.gov.uk/news-alerts/alert/FSA-AA-50-2026-update-1/",
        ),
        item("FSA home page", "https://alerts.food.gov.uk/"),
        *[item(f"extra {n}", RECALL_PAGE) for n in range(12)],
    ]
    kept, unmatched, dropped = check(ContextDraft(items=items), found(items, cited=(RECALL_PAGE,)))
    assert [k.title for k in kept][:1] == ["Walkers recall"]
    assert kept[0].cited
    assert kept[0].source_title == "Walkers recall"
    assert [u.title for u in unmatched] == ["FSA home page"]
    assert len(dropped) == 2
    assert dropped[0].reason == "over 12 items"


def test_relink_picks_only_from_retrieved_pages() -> None:
    items = [
        item("Walkers recall", "https://alerts.food.gov.uk/"),
        item("Made up", "https://x.test/"),
    ]
    spark = FakeSpark(found(items), [{"links": [{"item": 0, "page": 0}, {"item": 1, "page": 9}]}])
    kept, dropped = relink(spark, "muse-spark-1.3", items, found(items))
    assert [k.source_url for k in kept] == [RECALL_PAGE]
    assert kept[0].relinked
    assert [d.title for d in dropped] == ["Made up"]
    assert "no returned page says it" in dropped[0].reason
    prompt = str(spark.structured_calls[0]["messages"][1]["content"])
    assert "[0] Walkers recall" in prompt
    assert "(1) Made up" in prompt


def test_relink_without_sources_or_items() -> None:
    spark = FakeSpark(found([]), [])
    assert relink(spark, "m", [], found([])) == ([], [])
    empty: Grounded[ContextDraft] = Grounded(answer=ContextDraft())
    kept, dropped = relink(spark, "m", [item("a", "https://a.test/")], empty)
    assert kept == []
    assert len(dropped) == 1
    assert spark.structured_calls == []


def service(tmp_path: Path, spark: Any, location: SiteLocation | None = None) -> SiteContextService:
    return SiteContextService(
        spark,
        clock=FakeClock(),
        out_dir=tmp_path / "context",
        site_name="Corner supermarket",
        location=location or SiteLocation(city="Manchester", country="GB"),
    )


def test_refresh_checks_relinks_stores_and_reloads(tmp_path: Path) -> None:
    items = [
        item("Walkers recall", RECALL_PAGE),
        item("Bonfire night", "https://www.visitmanchester.com/", kind="event"),
        item("Weather", FORECAST, kind="weather"),
    ]
    spark = FakeSpark(
        found(items), [{"links": [{"item": 0, "page": 1}, {"item": 1, "page": None}]}]
    )
    svc = service(tmp_path, spark)
    context = svc.refresh("supermarket", notes="Oxford Road store")
    assert [(i.title, i.relinked) for i in context.items] == [
        ("Walkers recall", False),
        ("Bonfire night", True),
    ]
    assert [d.title for d in context.dropped] == ["Weather"]
    assert (context.searches, context.pages_seen) == (3, 2)
    call = spark.grounded_calls[0]
    assert call["user_location"] == {"city": "Manchester", "country": "GB"}
    assert "recalls" in call["messages"][1]["content"]
    assert "Oxford Road store" in call["messages"][1]["content"]
    assert svc.latest("supermarket") == context
    assert svc.latest("festival") is None


def test_refresh_needs_a_location(tmp_path: Path) -> None:
    with pytest.raises(ContextError):
        service(tmp_path, FakeSpark(found([]), []), SiteLocation()).refresh("festival")
    assert SiteLocation().describe() == "unknown"


def test_routes(tmp_path: Path) -> None:
    items = [item("Walkers recall", RECALL_PAGE)]
    svc = service(tmp_path, FakeSpark(found(items), []))
    app = FastAPI()
    app.include_router(context_router(svc))
    with TestClient(app) as client:
        assert client.get("/context/festival").status_code == 404
        reply = client.post("/context/supermarket", json={"notes": "n"})
        assert reply.status_code == 200
        assert reply.json()["items"][0]["source_url"] == RECALL_PAGE
        assert client.get("/context/supermarket").json()["vertical"] == "supermarket"
        assert client.get("/context/office").status_code == 422
        bad = {"location": {"country": "Great Britain"}}
        assert client.post("/context/festival", json=bad).status_code == 422
        empty = {"location": {"timezone": "Europe/London"}}
        assert client.post("/context/festival", json=empty).status_code == 422
    off = FastAPI()
    off.include_router(context_router(None))
    with TestClient(off) as client:
        assert client.get("/context/festival").status_code == 503


def test_route_reports_model_failures(tmp_path: Path) -> None:
    class Failing(FakeSpark):
        def grounded(self, **kwargs: Any) -> Grounded[ContextDraft]:
            raise ModelResponseError("boom")

    app = FastAPI()
    app.include_router(context_router(service(tmp_path, Failing(found([]), []))))
    with TestClient(app) as client:
        assert client.post("/context/festival").status_code == 502
