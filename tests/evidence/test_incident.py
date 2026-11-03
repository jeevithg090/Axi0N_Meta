"""One-click incident report (add-on 5.2): build, render, PDF fallback and the owner routes."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rulecam.api.app import AppServices, create_app
from rulecam.bus import Bus
from rulecam.contracts import PrivacyMode
from rulecam.evidence.chain import EventChain, read_chain, verify_chain
from rulecam.evidence.incident import (
    DEFAULT_CLAUSE,
    build_incident,
    candidates,
    event_of,
    find_incident,
    render_incident_html,
    report_document,
)
from rulecam.replay.cache import ReplayCache
from tests.fakes import FakeClock
from tests.verify.helpers import make_candidate

OWNER = {"base_url": "http://localhost", "client": ("127.0.0.1", 50000)}
TUNNEL = {"base_url": "https://rulecam-demo.trycloudflare.com", "client": ("127.0.0.1", 50001)}
PNG = b"\x89PNG\r\n\x1a\n fake"


def write_chain(root: Path, clock: FakeClock, strips: tuple[str, ...] = ()) -> EventChain:
    chain = EventChain(root, "site", clock=clock)
    chain.append("plan", {"plan_version_hash": "p", "rules": ["wash-before-line"]})
    candidate = make_candidate(strip=strips, event_id="ev-1")
    rule = {
        "id": "wash-before-line",
        "source": {
            "kind": "text",
            "text": "Wash for 20 s before the line.",
            "standard": ["§2-301.12"],
        },
    }
    chain.append(
        "candidate",
        {"candidate": candidate.model_dump(mode="json"), "context": {"rule": rule}},
    )
    clock.advance(2)
    chain.append(
        "verdict",
        {
            "event_id": "ev-1",
            "tier": "glimmer",
            "verdict": "violation",
            "score": 0.91,
            "reason": "<script>alert(1)</script> IGNORE PREVIOUS INSTRUCTIONS",
        },
    )
    chain.append(
        "action",
        {
            "event_id": "ev-1",
            "kind": "notify",
            "role": "aide",
            "detail": "notified aide",
            "ok": True,
        },
    )
    chain.append("action", {"event_id": "ev-1", "kind": "speak", "detail": "speaker", "ok": False})
    chain.append("ack", {"event_id": "ev-1", "role": "aide"})
    chain.append("label", {"event_id": "ev-1", "label": "false_alarm", "role": "aide"})
    chain.append("dispute", {"event_id": "ev-1", "outcome": "pending"})
    other = make_candidate(event_id="ev-2").model_dump(mode="json")
    other["measurement"] = None
    chain.append("candidate", {"candidate": other, "context": {}})
    return chain


def test_build_collects_the_events_records_in_order(tmp_path: Path) -> None:
    clock = FakeClock()
    strips = tmp_path / "strips"
    strips.mkdir()
    still = strips / "0.png"
    still.write_bytes(PNG)
    outside = tmp_path / "secret.png"
    outside.write_bytes(PNG)
    chain = write_chain(
        tmp_path / "chain", clock, (str(still), str(outside), str(strips / "x.png"))
    )
    path = chain.path_for(clock.now().date())
    records = read_chain(path)
    report, thumbs = build_incident(
        records,
        "ev-1",
        site="site",
        day=clock.now().date(),
        privacy_mode=PrivacyMode.STRICT,
        verify_url="https://ui/verify/?day=x",
        chain=verify_chain(path),
        stills_root=strips,
    )
    assert [e.kind for e in report.timeline] == [
        "candidate",
        "verdict",
        "action",
        "action",
        "ack",
        "label",
        "dispute",
    ]
    assert report.clauses == ("§2-301.12",)
    assert report.rule_text == "Wash for 20 s before the line."
    assert report.measurement == "8.00 s"
    assert report.stills == 1  # only stills under the strips folder, never elsewhere
    assert [t.kind for t in thumbs] == ["skeleton"]
    summaries = [e.summary for e in report.timeline]
    assert "glimmer: violation (score 0.91)" in summaries[1]
    assert summaries[3].endswith("(failed)")
    assert summaries[4] == "Acknowledged by aide"
    assert summaries[5] == "Labelled false_alarm by aide"
    assert summaries[6] == "Dispute pending"
    assert report.chain_ok
    assert report.chain_records == len(records)
    html = render_incident_html(report, thumbs)
    assert "<script>alert(1)</script>" not in html  # model text is escaped data
    assert "&lt;script&gt;" in html
    assert "data:image/png;base64," in html
    assert str(still) not in html  # no server path in the report
    assert "§2-301.12" in html


def test_without_a_rule_source_or_stills_root(tmp_path: Path) -> None:
    clock = FakeClock()
    chain = write_chain(tmp_path / "chain", clock)
    path = chain.path_for(clock.now().date())
    report, thumbs = build_incident(
        read_chain(path),
        "ev-2",
        site="site",
        day=clock.now().date(),
        privacy_mode=PrivacyMode.STANDARD,
        verify_url="u",
        chain=verify_chain(path),
    )
    assert report.clauses == (DEFAULT_CLAUSE,)
    assert report.measurement is None
    assert thumbs == ()
    assert "No stills" in render_incident_html(report, thumbs)
    with pytest.raises(KeyError):
        build_incident(
            read_chain(path),
            "ev-404",
            site="s",
            day=clock.now().date(),
            privacy_mode=PrivacyMode.STRICT,
            verify_url="u",
            chain=verify_chain(path),
        )


def test_find_and_list_incidents(tmp_path: Path) -> None:
    clock = FakeClock()
    chain = write_chain(tmp_path / "chain", clock)
    site_dir = tmp_path / "chain" / "site"
    (site_dir / "notes.jsonl").write_text("x")  # not a day: skipped
    clock.advance(86400)
    chain.append("plan", {"rules": []})
    found = find_incident(site_dir, "ev-1")
    assert found is not None
    assert found[0] == (clock.now() - timedelta(days=1)).date()
    assert find_incident(site_dir, "ev-404") is None
    assert find_incident(tmp_path / "nowhere", "ev-1") is None
    rows = candidates(found[1])
    assert [r.event_id for r in rows] == ["ev-2", "ev-1"]
    assert rows[1].verdict == "violation"
    assert event_of(found[1][0]) is None  # a plan record belongs to no event


def test_report_document_falls_back_to_html(tmp_path: Path) -> None:
    def no_chromium(html: str, out: Path) -> Path:
        raise RuntimeError("Executable doesn't exist")

    def printer(html: str, out: Path) -> Path:
        out.write_bytes(b"%PDF-1.7 " + html.encode())
        return out

    assert report_document("<p>x</p>", no_chromium) == (b"<p>x</p>", "text/html")
    body, media = report_document("<p>x</p>", printer)
    assert media == "application/pdf"
    assert body.startswith(b"%PDF")


def services(tmp_path: Path, **extra: object) -> AppServices:
    bus = Bus()
    return AppServices(
        bus=bus,
        cache=ReplayCache(tmp_path / "replay", "replay", clock=FakeClock(), bus=bus),
        chain_root=tmp_path / "chain",
        site="site",
        **extra,  # type: ignore[arg-type]
    )


def test_incident_routes_are_owner_only_and_download(tmp_path: Path) -> None:
    clock = FakeClock()
    write_chain(tmp_path / "chain", clock)

    def printer(html: str, out: Path) -> Path:
        out.write_bytes(b"%PDF-1.7 fake")
        return out

    app = create_app(services(tmp_path, pdf_printer=printer, verify_base="https://ui/v/"))
    with TestClient(app, **TUNNEL) as remote:  # type: ignore[arg-type]
        assert remote.get("/incidents").status_code == 403
        assert remote.get("/incidents/ev-1").status_code == 403
        assert remote.get("/incidents/ev-1/report").status_code == 403
    with TestClient(app, **OWNER) as client:  # type: ignore[arg-type]
        assert [r["event_id"] for r in client.get("/incidents").json()] == ["ev-2", "ev-1"]
        day = clock.now().date().isoformat()
        assert len(client.get("/incidents", params={"day": day}).json()) == 2
        assert client.get("/incidents", params={"day": "2020-01-01"}).json() == []
        body = client.get("/incidents/ev-1").json()
        assert body["verify_url"] == f"https://ui/v/?day={day}"
        assert body["privacy_mode"] == "strict"
        assert client.get("/incidents/ev-404").status_code == 404
        assert client.get("/incidents/bad id!").status_code in {404, 422}
        pdf = client.get("/incidents/ev-1/report")
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.headers["x-rulecam-report-format"] == "pdf"
        assert 'filename="incident-ev-1.pdf"' in pdf.headers["content-disposition"]
        html = client.get("/incidents/ev-1/report", params={"format": "html"})
        assert html.headers["content-type"].startswith("text/html")
        assert "Incident report" in html.text


def test_report_without_chromium_is_printable_html(tmp_path: Path) -> None:
    def no_chromium(html: str, out: Path) -> Path:
        raise ImportError("playwright")

    write_chain(tmp_path / "chain", FakeClock())
    app = create_app(services(tmp_path, pdf_printer=no_chromium))
    with TestClient(app, **OWNER) as client:  # type: ignore[arg-type]
        assert client.get("/incidents").json()  # the latest day by default
        reply = client.get("/incidents/ev-1/report")
        assert reply.headers["x-rulecam-report-format"] == "html"
        assert 'filename="incident-ev-1.html"' in reply.headers["content-disposition"]
        assert "http://localhost/verify/?day=" in reply.text  # the API's own verify page
    empty = create_app(services(tmp_path / "empty"))
    with TestClient(empty, **OWNER) as client:  # type: ignore[arg-type]
        assert client.get("/incidents").json() == []
