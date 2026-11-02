"""Add-on 1.4: blind verifier labelling sheet, label scoring and the old strip format."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.eval.errors import EvalError
from rulecam.eval.verifier_set import (
    blind,
    candidate_for,
    cohen_kappa,
    collect_items,
    downgrade_strip,
    load_key,
    pick_frames,
    read_labels,
    render_viewer,
    rule_ir,
    score_labels,
    strip_dir,
    write_sheet,
)

REPO = Path(__file__).resolve().parents[2]
CAMPAIGN = [
    ("run1", REPO / "reports" / "eval" / "2026-11-01-live-test-campaign-run1.json"),
    ("run2", REPO / "reports" / "eval" / "2026-11-01-live-test-campaign.json"),
]
cli = CliRunner()

RULE = {
    "id": "keep-4m",
    "concepts": {"person": {"prompt": "person"}, "forklift": {"prompt": "forklift"}},
    "predicate": {"op": "distance_m", "a": "person", "b": "forklift", "lt": 4.0},
    "severity": "high",
    "calibration": "ground-plane@phone-50",
    "verify": {"tier1": "glimmer"},
    "accept": [],
}


def event(n: int, verdict: str) -> dict[str, object]:
    return {
        "event_id": f"ev-{n}",
        "rule_id": "keep-4m",
        "ts_start": "2026-11-01T21:07:26.685202Z",
        "ts_end": "2026-11-01T21:07:28.585202Z",
        "measurement": {"value": 2.9, "unit": "m"},
        "trace": {"all": True, "subject": "phone-50:person:b7", "nested": {"x": 1}},
        "verdict": {"verdict": verdict, "score": 0.9, "reason": "SECRET-REASON"},
    }


def campaign(tmp_path: Path) -> Path:
    tests = [
        {
            "id": "T02",
            "camera_id": "phone-50",
            "request": "Keep people at least 4 metres away from a moving forklift.",
            "rules": [RULE],
            "events": [event(1, "violation"), event(2, "no_violation"), {"event_id": "x"}],
        },
        {"id": "T01", "camera_id": "phone-49", "request": "r", "rules": [], "events": []},
    ]
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(tests), encoding="utf-8")
    return path


def strip(root: Path, event_id: str, frames: int, width: int = 960) -> None:
    folder = strip_dir(root, "phone-50", event_id)
    folder.mkdir(parents=True)
    for k in range(1, frames + 1):
        image = np.full((width * 9 // 16, width, 3), 20 * k, np.uint8)
        assert cv2.imwrite(str(folder / f"{k}.jpg"), image)


def test_collects_the_29_campaign_verdicts() -> None:
    items = collect_items(CAMPAIGN)
    assert len(items) == 29
    assert sum(i.run == "run1" for i in items) == 2
    assert {i.verdict for i in items} == {"violation", "no_violation", "unsure"}


def test_collect_errors(tmp_path: Path) -> None:
    with pytest.raises(EvalError, match="cannot read"):
        collect_items([("x", tmp_path / "missing.json")])
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            [
                {
                    "id": "T",
                    "camera_id": "c",
                    "request": "r",
                    "rules": [],
                    "events": [event(1, "violation")],
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvalError, match="has no rule"):
        collect_items([("x", bad)])


def test_sheet_is_blind(tmp_path: Path) -> None:
    items = blind(collect_items([("run2", campaign(tmp_path))]), seed=7)
    assert [i.item for i in items] == ["v01", "v02"]
    strips = tmp_path / "strips"
    strip(strips, items[0].event_id, 8)
    out = tmp_path / "sheet"
    done, missing = write_sheet(items, out, strips)
    assert missing == ["v02"]
    assert len(done[0].strips) == 8
    assert (out / "strips" / "v01" / "8.jpg").is_file()
    viewer = (out / "index.html").read_text(encoding="utf-8")
    labels = (out / "labels.csv").read_text(encoding="utf-8")
    for hidden in ("SECRET-REASON", 'no_violation"', "0.9", "ev-1"):
        assert hidden not in labels
    assert "SECRET-REASON" not in viewer
    assert "ev-1" not in viewer
    assert "No strip was found" in viewer
    key = load_key(out / "key.json")
    assert {k.item: k.verdict for k in key}.keys() == {"v01", "v02"}
    with pytest.raises(EvalError, match="cannot read key"):
        load_key(tmp_path / "nope.json")


def test_viewer_escapes_the_request() -> None:
    items = blind(collect_items(CAMPAIGN), seed=1)[:1]
    page = render_viewer([items[0].__class__(**{**items[0].__dict__, "request": "<b>x</b>"})])
    assert "&lt;b&gt;x&lt;/b&gt;" in page


def write_labels(path: Path, labels: dict[str, str]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["item", "request", "label", "notes"])
        writer.writerows([k, "r", v, ""] for k, v in labels.items())
    return path


def test_read_labels(tmp_path: Path) -> None:
    path = write_labels(tmp_path / "a.csv", {"v1": "Violation", "v2": "no violation", "v3": ""})
    assert read_labels(path) == {"v1": "violation", "v2": "no_violation"}
    write_labels(path, {"v1": "maybe"})
    with pytest.raises(EvalError, match="unknown label"):
        read_labels(path)
    path.write_text("x,y\n1,2\n", encoding="utf-8")
    with pytest.raises(EvalError, match="columns"):
        read_labels(path)
    with pytest.raises(EvalError, match="cannot read"):
        read_labels(tmp_path / "missing.csv")


def test_kappa() -> None:
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == pytest.approx(1.0)
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0.0)
    assert cohen_kappa(["a", "a"], ["a", "a"]) is None  # no chance to disagree
    assert cohen_kappa([], []) is None


def test_score_against_two_labellers() -> None:
    glimmer = {"v1": "violation", "v2": "no_violation", "v3": "unsure", "v4": "violation"}
    alice = {"v1": "violation", "v2": "violation", "v3": "violation", "v4": "unsure"}
    bob = {"v1": "violation", "v2": "no_violation", "v3": "violation", "v4": "no_violation"}
    report = score_labels(glimmer, {"alice": alice, "bob": bob})
    assert report.items == 4
    assert report.glimmer_unsure_rate == 0.25
    assert report.labeller_unsure_rate == {"alice": 0.25, "bob": 0.0}
    assert report.agreement == 0.5
    assert report.cohen_kappa is not None
    by_ref = {a.reference: a for a in report.accuracy}
    assert by_ref["alice"].items == 3
    assert by_ref["alice"].correct == 1
    assert by_ref["alice"].unsure == 1
    assert by_ref["alice"].accuracy_when_decisive == 0.5
    consensus = by_ref["consensus"]  # v1 and v3: both labelled violation
    assert (consensus.items, consensus.correct, consensus.unsure) == (2, 1, 1)
    assert consensus.accuracy == 0.5
    assert json.loads(report.to_json())["items"] == 4
    alone = score_labels(glimmer, {"alice": {}})
    assert alone.agreement is None
    assert alone.accuracy[0].accuracy is None
    assert alone.accuracy[0].accuracy_when_decisive is None


def test_pick_frames() -> None:
    assert pick_frames(8, 6) == [0, 1, 3, 4, 6, 7]
    assert pick_frames(4, 6) == [0, 1, 2, 3]
    assert pick_frames(8, 1) == [7]


def test_downgrade_strip(tmp_path: Path) -> None:
    strip(tmp_path / "s", "ev-1", 8)
    frames = sorted((tmp_path / "s").rglob("*.jpg"), key=lambda p: int(p.stem))
    calls = []

    def blur(image: np.ndarray) -> np.ndarray:
        calls.append(image.shape)
        return image

    out = downgrade_strip(frames, tmp_path / "old", blur=blur)
    assert len(out) == 6
    assert calls[0][1] == 640
    small = cv2.imread(str(out[0]))
    assert small is not None
    assert small.shape[1] == 640
    (tmp_path / "bad.jpg").write_bytes(b"not a jpeg")
    with pytest.raises(EvalError, match="cannot read"):
        downgrade_strip([tmp_path / "bad.jpg"], tmp_path / "x", blur=blur)


def test_candidate_for_replay(tmp_path: Path) -> None:
    item = collect_items([("run2", campaign(tmp_path))])[0]
    rule = rule_ir(item)
    assert rule.source.text == item.request
    candidate = candidate_for(item, [tmp_path / "1.jpg"], variant="blur6_640")
    assert candidate.event_id == "ev-1-blur6_640"
    assert candidate.severity == "high"
    assert "nested" not in candidate.predicate_trace
    assert candidate.measurement is not None
    assert candidate.ts_start.tzinfo is not None


def test_cli_sheet_and_score(tmp_path: Path) -> None:
    out = tmp_path / "sheet"
    spec = f"run2={campaign(tmp_path)}"
    args = ["eval", "verifier-sheet", "--campaign", spec, "--strips", str(tmp_path), "--out"]
    result = cli.invoke(app, [*args, str(out)])
    assert result.exit_code == 0, result.output
    assert "2 verdicts" in result.output
    assert cli.invoke(app, ["eval", "verifier-sheet", "--campaign", "nope"]).exit_code == 2
    bad = ["eval", "verifier-sheet", "--campaign", f"x={tmp_path / 'none.json'}"]
    assert cli.invoke(app, bad).exit_code == 2
    a = write_labels(tmp_path / "alice.csv", {"v01": "violation", "v02": "violation"})
    b = write_labels(tmp_path / "bob.csv", {"v01": "violation", "v02": "no_violation"})
    report = tmp_path / "score.json"
    score = ["eval", "verifier-score", str(a), str(b), "--key", str(out / "key.json")]
    result = cli.invoke(app, [*score, "--out", str(report)])
    assert result.exit_code == 0, result.output
    assert json.loads(report.read_text(encoding="utf-8"))["agreement"] == 0.5
    missing = ["eval", "verifier-score", str(a), "--key", str(tmp_path / "none.json")]
    assert cli.invoke(app, missing).exit_code == 2
