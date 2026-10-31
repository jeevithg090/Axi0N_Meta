"""Tests for Merkle roots, evidence packs and the audit (P3-07b)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.contracts import PrivacyMode
from rulecam.evidence.audit import audit_chain
from rulecam.evidence.chain import EventChain
from rulecam.evidence.merkle import (
    EMPTY_ROOT,
    chain_root,
    day_roots,
    merkle_root,
    read_day_roots,
    write_day_roots,
)
from rulecam.evidence.packs import (
    EvidencePack,
    PackRow,
    Thumbnail,
    allowed_thumbnails,
    render_html,
    wilson_interval,
)
from rulecam.rules.backtest import DecisionContext, candidate_payload
from rulecam.rules.store import ParquetTrackStore
from tests.fakes import FakeClock, InMemoryTrackStore
from tests.rules.helpers import CAL, INSIDE_M, SINK_ZONE, at, make_rule, walk_track

DAY = date(2026, 10, 30)


def h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- Merkle -----------------------------------------------------------------------------------


def test_merkle_root_shapes() -> None:
    a, b, c = h("a"), h("b"), h("c")
    assert merkle_root([]) == EMPTY_ROOT
    assert merkle_root([a]) == a
    assert merkle_root([a, b]) == h(a + b)
    assert merkle_root([a, b, c]) == h(h(a + b) + h(c + c))
    assert merkle_root([a, b, c]) == merkle_root([a, b, c])
    assert merkle_root([b, a]) != merkle_root([a, b])


def test_day_roots_file_round_trip(tmp_path: Path) -> None:
    clock = FakeClock()
    for site in ("kitchen", "yard"):
        chain = EventChain(tmp_path / "chain", site, clock=clock)
        chain.append("plan", {"site": site})
        chain.append("ack", {"role": "lead"})
    path = write_day_roots(tmp_path / "chain", tmp_path / "roots", DAY)
    assert path.name == "2026-10-30.txt"
    roots = read_day_roots(path)
    assert set(roots) == {"kitchen", "yard"}
    assert roots["kitchen"] == chain_root(tmp_path / "chain" / "kitchen" / "2026-10-30.jsonl")
    assert roots["kitchen"][1] == 2
    with pytest.raises(FileNotFoundError):
        write_day_roots(tmp_path / "chain", tmp_path / "roots", date(2026, 1, 1))
    assert day_roots(tmp_path / "missing", DAY) == {}


def test_cli_evidence_root(tmp_path: Path) -> None:
    EventChain(tmp_path / "chain", "s", clock=FakeClock()).append("plan", {})
    result = CliRunner().invoke(
        app,
        [
            "evidence",
            "root",
            "2026-10-30",
            "--chain-dir",
            str(tmp_path / "chain"),
            "--roots-dir",
            str(tmp_path / "roots"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "roots" / "2026-10-30.txt").read_text().startswith("s ")
    for day in ("2026-01-01", "yesterday"):
        missing = CliRunner().invoke(
            app, ["evidence", "root", day, "--chain-dir", str(tmp_path / "chain")]
        )
        assert missing.exit_code == 2, missing.output


# --- packs ------------------------------------------------------------------------------------


def test_wilson_interval() -> None:
    lo, hi = wilson_interval(45, 50)
    assert lo == pytest.approx(0.7864, abs=1e-3)
    assert hi == pytest.approx(0.9565, abs=1e-3)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    assert wilson_interval(10, 10)[1] == pytest.approx(1.0)
    with pytest.raises(ValueError, match="invalid counts"):
        wilson_interval(5, 3)


THUMBS = (
    Thumbnail(path="data/strips/a_skeleton.png", kind="skeleton"),
    Thumbnail(path="data/strips/a_masked.png", kind="masked"),
    Thumbnail(path="data/strips/a_raw.png", kind="raw"),
)


def make_pack(
    mode: PrivacyMode, template: str = "food_code", chain_ok: bool = True
) -> EvidencePack:
    rows = (
        PackRow(
            clause="Food Code §3-501.16 cold holding",
            rule_id="cold",
            rule_text="not observable",
            checks=0,
            passes=0,
            watched_fraction=0.0,
        ),
        PackRow(
            clause="Food Code §2-301.12",
            rule_id="wash-before-line",
            rule_text="Wash <hands> ≥ 20 s before the line",
            checks=50,
            passes=45,
            watched_fraction=0.92,
            thumbnails=THUMBS,
            corrective_actions=("Coaching at the sink (es)", "Shift lead notified"),
        ),
        PackRow(
            clause="Food Code §2-103.11(G)",
            rule_id="monitoring",
            rule_text="PIC monitors",
            checks=10,
            passes=10,
            watched_fraction=0.9,
        ),
    )
    return EvidencePack(
        template=template,
        site="Main St kitchen",
        period="2026-10-30",
        privacy_mode=mode,
        rows=rows,
        verify_url="https://rulecam.test/verify/?day=2026-10-30",
        chain_records=117,
        chain_ok=chain_ok,
    )


def test_thumbnails_respect_privacy_mode() -> None:
    assert [t.kind for t in allowed_thumbnails(THUMBS, PrivacyMode.ANONYMOUS)] == ["skeleton"]
    assert [t.kind for t in allowed_thumbnails(THUMBS, PrivacyMode.STRICT)] == ["skeleton"]
    assert [t.kind for t in allowed_thumbnails(THUMBS, PrivacyMode.STANDARD)] == [
        "skeleton",
        "masked",
    ]


def test_anonymous_pack_has_watched_fraction_and_no_raw_paths() -> None:
    html = render_html(make_pack(PrivacyMode.ANONYMOUS))
    assert "92%" in html
    assert "a_skeleton.png" in html
    assert "a_masked.png" not in html
    assert "a_raw.png" not in html
    assert ".mp4" not in html
    assert "data:image/png;base64," in html  # the QR code


def test_food_code_pack_leads_with_monitoring_then_handwashing() -> None:
    html = render_html(make_pack(PrivacyMode.STANDARD))
    first, second, third = (
        html.index(c) for c in ("§2-103.11(G)</td>", "§2-301.12</td>", "§3-501.16")
    )
    assert first < second < third
    assert "90% (79% to 96%)" in html
    assert "no checks" in html
    assert "Wash &lt;hands&gt;" in html  # auto-escaped
    assert "Evidence chain intact: 117 records." in html


@pytest.mark.parametrize("template", ["f689", "generic"])
def test_other_templates_and_broken_chain(template: str) -> None:
    html = render_html(make_pack(PrivacyMode.STRICT, template, chain_ok=False))
    assert "Evidence chain failed verification." in html
    assert ("F689" in html) is (template == "f689")


# --- audit ------------------------------------------------------------------------------------


def chain_with_candidate(tmp_path: Path, store: InMemoryTrackStore) -> Path:
    rule = make_rule({"op": "inside", "object": "person", "zone": "sink_zone"}, rule_id="in-sink")
    store.append([walk_track("cam1:person:1", [INSIDE_M] * 20)])
    context = DecisionContext(
        rule=rule, calibration=CAL, zones={"sink_zone": SINK_ZONE}, t0=at(0), t1=at(3)
    )
    (candidate,) = context.evaluate(store.window("cam1", at(0), at(3)))
    clock = FakeClock()
    chain = EventChain(tmp_path, "site", clock=clock)
    chain.append("plan", {})
    chain.append("candidate", candidate_payload(candidate, context))
    return chain.path_for(clock.now().date())


def test_audit_reproduces_decisions(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    path = chain_with_candidate(tmp_path, store)
    report = audit_chain(path, store)
    assert report.ok
    assert report.summary() == "chain intact (2 records); 1/1 decisions reproduced"


def test_audit_flags_changed_tracks_and_broken_chain(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    path = chain_with_candidate(tmp_path, store)
    assert not audit_chain(path, InMemoryTrackStore()).ok  # tracks gone: not reproduced
    lines = path.read_text().splitlines()
    record = json.loads(lines[1])
    record["payload"]["candidate"]["camera_id"] = "cam9"
    lines[1] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n")
    report = audit_chain(path, store)
    assert not report.ok
    assert report.summary().startswith("chain broken at event 2")


def test_cli_audit(tmp_path: Path) -> None:
    store_dir = tmp_path / "tracks"
    parquet = ParquetTrackStore(store_dir)
    memory = InMemoryTrackStore()
    path = chain_with_candidate(tmp_path / "chain", memory)
    parquet.append(memory.window("cam1", at(0), at(3)))
    parquet.close()
    args = [
        "audit",
        path.stem,
        "site",
        "--store-dir",
        str(store_dir),
        "--chain-dir",
        str(tmp_path / "chain"),
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "1/1 decisions reproduced" in result.output
    empty = CliRunner().invoke(
        app,
        [*args[:3], "--store-dir", str(tmp_path / "none"), "--chain-dir", str(tmp_path / "chain")],
    )
    assert empty.exit_code == 1
    assert "not reproduced: event 2" in empty.output
    chain_args = ["--chain-dir", str(tmp_path / "chain")]
    for day, site in (("2026-01-01", "site"), ("30/10/2026", "site"), (path.stem, "..")):
        missing = CliRunner().invoke(app, ["audit", day, site, *chain_args])
        assert missing.exit_code == 2, missing.output
    path.write_text(path.read_text().replace('"plan"', '"ack"', 1))
    broken = CliRunner().invoke(app, args)
    assert broken.exit_code == 1
    assert "chain broken at event 1" in broken.output
