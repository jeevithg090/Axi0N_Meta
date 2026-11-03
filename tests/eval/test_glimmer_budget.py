"""Glimmer budget sweep (add-on 2.2): recorded strips asked again at several budgets, offline."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import pytest
from tests.demo import synthetic
from tests.fakes import FakeClock

from rulecam.clients.open_weights.glimmer import GlimmerClient, GlimmerConfig
from rulecam.config import Settings
from rulecam.demo.fixture import CHAIN_FILE, STRIPS_DIR
from rulecam.eval.glimmer_budget import (
    SweepReport,
    Trial,
    read_cases,
    read_labels,
    render_table,
    strip_frames,
    summarise,
    sweep,
)

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "eval" / "glimmer_budget_sweep.py"


@pytest.fixture
def fixture(tmp_path: Path) -> Path:
    out = tmp_path / "golden"
    synthetic.build(REPO / "scenarios" / "warehouse_driver", out)
    return out


class Glimmer:
    """A fake llama-server: the reply depends on the budget it was sent."""

    def __init__(self) -> None:
        self.bodies: list[Mapping[str, object]] = []

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        body = request["json"]
        assert isinstance(body, Mapping)
        self.bodies.append(body)
        budget = body.get("reasoning_budget_tokens")
        if budget == 0 and body["max_tokens"] == 1024:
            message: dict[str, object] = {"content": None}
            return _reply(message, "length", 1024)
        verdict = "unsure" if budget == 0 else "violation"
        content = {"verdict": verdict, "p_violation": 0.8, "reason": "r", "scene_text": ""}
        message = {"content": json.dumps(content), "reasoning_content": f"thought {budget}"}
        return _reply(message, "stop", 40 if budget == 0 else 400)


def _reply(message: dict[str, object], finish: str, tokens: int) -> dict[str, object]:
    usage = {
        "prompt_tokens": 2000,
        "completion_tokens": tokens,
        "completion_tokens_details": {"reasoning_tokens": max(0, tokens - 40)},
    }
    choice = {"message": message, "finish_reason": finish}
    return {"status": 200, "headers": {}, "json": {"choices": [choice], "usage": usage}}


def test_the_chain_candidates_are_asked_at_every_budget(fixture: Path, tmp_path: Path) -> None:
    cases, skipped = read_cases([fixture / CHAIN_FILE], fixture / STRIPS_DIR)
    assert (len(cases), skipped) == (1, 6), "the committed day chain has 6 strip-less candidates"
    (case,) = cases
    assert case.recorded == "violation"
    assert [Path(p).name for p in case.candidate.strip_paths] == ["1.jpg", "2.jpg"]

    server = Glimmer()

    def client_for(budget: int) -> GlimmerClient:
        return GlimmerClient(
            server, clock=FakeClock(), config=GlimmerConfig(reasoning_budget_tokens=budget)
        )

    labels = {case.candidate.event_id: "no_violation"}
    summaries, trials = sweep(cases, [0, 256, -1], client_for, labels=labels, repeats=2)

    assert [t.budget for t in trials] == [0, 256, -1] * 2
    assert [b.get("reasoning_budget_tokens") for b in server.bodies[:4]] == [0, 0, 256, None]
    zero, mid, server_default = summaries
    assert (zero.trials, zero.decisive, zero.retried_cut, zero.agree_recorded) == (2, 0, 2, 0)
    assert (mid.decisive, mid.agree_recorded, mid.compared_recorded) == (2, 2, 2)
    assert (mid.agree_label, mid.labelled) == (0, 2)
    assert mid.completion_tokens_p50 == 400
    assert mid.reasoning_tokens_p50 == 360
    assert server_default.errors == 0
    assert trials[0].max_tokens == 2048
    assert trials[0].reasoning_ref is None, "no reasoning store in this client"


def test_failures_are_recorded_not_raised(fixture: Path) -> None:
    cases, _ = read_cases([fixture / CHAIN_FILE], fixture / STRIPS_DIR)

    def broken(request: Mapping[str, object]) -> Mapping[str, object]:
        return {"status": 200, "headers": {}, "json": {"choices": []}}

    summaries, trials = sweep(cases, [128], lambda b: GlimmerClient(broken, clock=FakeClock()))
    assert trials[0].error is not None
    assert "ModelResponseError" in trials[0].error
    assert summaries[0].errors == 1
    assert summaries[0].latency_ms_p50 is None


def test_strips_are_found_in_either_layout(tmp_path: Path) -> None:
    live = tmp_path / "live" / "cam" / "ev"
    live.mkdir(parents=True)
    for name in ("10.jpg", "2.jpg", "notes.jpg"):
        (live / name).write_bytes(b"x")
    assert [p.name for p in strip_frames(tmp_path / "live", "cam", "ev")] == ["2.jpg", "10.jpg"]
    golden = tmp_path / "golden" / "ev"
    golden.mkdir(parents=True)
    (golden / "1.jpg").write_bytes(b"x")
    assert [p.name for p in strip_frames(tmp_path / "golden", "cam", "ev")] == ["1.jpg"]
    assert strip_frames(tmp_path / "none", "cam", "ev") == []


def test_a_candidate_without_a_strip_is_skipped(fixture: Path, tmp_path: Path) -> None:
    cases, skipped = read_cases([fixture / CHAIN_FILE], tmp_path / "empty")
    assert (cases, skipped) == ([], 7)


def test_labels_must_be_verdicts(tmp_path: Path) -> None:
    assert read_labels(None) == {}
    good = tmp_path / "good.json"
    good.write_text('{"e1": "violation"}', "utf-8")
    assert read_labels(good) == {"e1": "violation"}
    bad = tmp_path / "bad.json"
    bad.write_text('{"e1": "maybe"}', "utf-8")
    with pytest.raises(ValueError, match="e1"):
        read_labels(bad)


def test_the_table_names_the_server_default() -> None:
    trial = Trial(event_id="e", budget=-1, verdict="violation", latency_ms=900, calls=1)
    report = SweepReport.model_validate(
        {
            "generated_at": "2026-11-02T12:00:00Z",
            "model": "muse-glimmer-30b",
            "max_tokens": 1024,
            "max_tokens_ceiling": 4096,
            "budgets": [-1],
            "cases": 1,
            "skipped_no_strip": 0,
            "summaries": [summarise(-1, [trial]).model_dump()],
            "trials": [trial.model_dump()],
        }
    )
    table = render_table(report)
    assert "| server | 1 | 0 | 1 |" in table
    assert "| 900 | 900 | - | - |" in table


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("glimmer_budget_sweep", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_script_writes_the_report_without_the_network(
    fixture: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _script()
    server = Glimmer()
    budgets: list[int] = []

    def offline(settings: Settings, **kwargs: object) -> GlimmerClient:
        budgets.append(settings.glimmer_reasoning_budget_tokens)
        config = GlimmerConfig(
            max_tokens=settings.glimmer_max_tokens,
            reasoning_budget_tokens=settings.glimmer_reasoning_budget_tokens,
        )
        return GlimmerClient(server, clock=FakeClock(), config=config)

    monkeypatch.setattr(GlimmerClient, "from_settings", offline)
    monkeypatch.setattr(
        script, "get_settings", lambda: Settings(_env_file=None, rulecam_data_dir=tmp_path / "d")
    )
    out = tmp_path / "sweep.json"
    script.main(
        [
            "--chain",
            str(fixture / CHAIN_FILE),
            "--strips",
            str(fixture / STRIPS_DIR),
            "--budgets",
            "128,-1",
            "--max-tokens",
            "1024",
            "--limit",
            "1",
            "--out",
            str(out),
        ]
    )
    report = json.loads(out.read_text("utf-8"))
    assert budgets == [128, -1]
    assert [s["budget"] for s in report["summaries"]] == [128, -1]
    assert report["cases"] == 1
    assert "| 128 |" in capsys.readouterr().out
