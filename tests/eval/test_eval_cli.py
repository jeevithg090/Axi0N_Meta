"""`rulecam eval validate|fetch|score|run` and `rulecam scorecard` (no network)."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import httpx
import pytest
import yaml
from tests.fakes.eval_api import SimClock
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.eval import runner as runner_module

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "eval" / "clips"
BASELINE = REPO / "eval" / "baseline" / "2026-10-31-live"
cli = CliRunner()


def test_validate_the_seed_set() -> None:
    result = cli.invoke(app, ["eval", "validate", "--set", str(SEED)])
    assert result.exit_code == 0, result.output
    assert "warehouse_45848: forklift_2m=negative" in result.output
    assert "2 runnable" in result.output


def test_validate_reports_errors(tmp_path: Path) -> None:
    result = cli.invoke(app, ["eval", "validate", "--set", str(tmp_path)])
    assert result.exit_code == 2


def test_score_replays_the_baseline_and_writes_the_scorecard(tmp_path: Path) -> None:
    checks = tmp_path / "checks.yaml"
    checks.write_text("owner_login: {passed: false, evidence: WS4 open}\n", encoding="utf-8")
    result = cli.invoke(
        app,
        [
            "eval",
            "score",
            str(BASELINE),
            "--set",
            str(SEED),
            "--out",
            str(tmp_path),
            "--checks",
            str(checks),
        ],
    )
    assert result.exit_code == 0, result.output
    (md,) = tmp_path.glob("*.md")
    text = md.read_text(encoding="utf-8")
    assert "Glimmer decisive (not unsure) on ≥ 85% of candidates | 0% (0/10) | FAIL" in text
    assert "0 on 1 negative clip runs (10 unverified candidates) | PASS" in text
    assert "| Owner login | no (WS4 open) | FAIL |" in text
    (report,) = tmp_path.glob("*.json")
    card = cli.invoke(
        app,
        [
            "scorecard",
            "--report",
            str(report),
            "--checks",
            str(checks),
            "--out",
            str(tmp_path / "card.md"),
        ],
    )
    assert card.exit_code == 0, card.output
    assert "0% (0/10)" in (tmp_path / "card.md").read_text(encoding="utf-8")


def test_score_without_runs_is_a_usage_error(tmp_path: Path) -> None:
    result = cli.invoke(
        app, ["eval", "score", str(tmp_path), "--set", str(SEED), "--out", str(tmp_path)]
    )
    assert result.exit_code == 2


def make_set(tmp_path: Path, video: bytes) -> Path:
    folder = tmp_path / "set" / "yard"
    folder.mkdir(parents=True)
    (folder / "clip.mp4").write_bytes(video)
    data = {
        "name": "yard",
        "camera_id": "yard",
        "source": {"path": "clip.mp4", "sha256": hashlib.sha256(video).hexdigest()},
        "duration_s": 6,
        "rules": [{"id": "forklift_2m", "request": "keep clear", "status": "negative"}],
    }
    (folder / "clip.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return tmp_path / "set"


def test_fetch_checks_local_clips(tmp_path: Path) -> None:
    set_dir = make_set(tmp_path, b"video")
    ok = cli.invoke(
        app, ["eval", "fetch", "--set", str(set_dir), "--cache-dir", str(tmp_path / "c")]
    )
    assert ok.exit_code == 0
    assert "yard:" in ok.output
    (set_dir / "yard" / "clip.mp4").write_bytes(b"changed")
    bad = cli.invoke(
        app, ["eval", "fetch", "--set", str(set_dir), "--cache-dir", str(tmp_path / "c")]
    )
    assert bad.exit_code == 1


def test_run_against_a_mock_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole `eval run` path, with httpx on a mock transport and no real sleeping."""
    set_dir = make_set(tmp_path, b"video")
    approved = {"activated": ["r"], "plan_version_hash": "h"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        routes = {
            "/pair/token": {"token": "t" * 20},
            f"/pair/{'t' * 20}/claim": {"camera_id": "phone-1", "label": "yard", "session": "s"},
            f"/pair/{'t' * 20}/chunk": {"chunk_id": "c", "n_frames": 30, "width": 64, "height": 48},
            "/plans/requests": {"plan_id": "plan-1"},
            "/plans/plan-1": {"plan_id": "plan-1", "rules": []},
            "/plans/plan-1/approve": approved,
            "/runtime/metrics": {"lag_s": 1.0, "in_flight": 0, "billed_frames": 0},
        }
        if path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404)

    real_client = httpx.Client

    def mock_client(**kwargs: object) -> httpx.Client:
        return real_client(base_url=str(kwargs["base_url"]), transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", mock_client)
    sim = SimClock()
    monkeypatch.setattr(runner_module, "Clock", sim.clock)
    monkeypatch.setattr(
        runner_module,
        "split_clip",
        lambda clip, out: [
            runner_module.LocalChunk(0, clip, 0.0, 3.0),
            runner_module.LocalChunk(1, clip, 3.0, 3.0),
        ],
    )
    result = cli.invoke(
        app,
        [
            "eval",
            "run",
            "--set",
            str(set_dir),
            "--out",
            str(tmp_path / "out"),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--survey-s",
            "0",
            "--no-bus",
            "--speed",
            "2.5",
        ],
    )
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "out" / "runs").glob("*/yard__forklift_2m.run.json"))
    assert list((tmp_path / "out").glob("*.md"))
    shutil.rmtree(tmp_path / "out")


def test_score_takes_cameras_per_gpu_from_the_site_config(tmp_path: Path) -> None:
    site = tmp_path / "site.yaml"
    site.write_text("cameras_per_gpu: 4\n", encoding="utf-8")
    base = ["eval", "score", str(BASELINE), "--set", str(SEED), "--gpu-usd-per-hour", "0.59"]

    def config(*extra: str) -> dict[str, object]:
        out = tmp_path / f"out{len(list(tmp_path.iterdir()))}"
        result = cli.invoke(app, [*base, "--out", str(out), *extra])
        assert result.exit_code == 0, result.output
        (report,) = out.glob("*.json")
        data = yaml.safe_load(report.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        return dict(data["config"])

    from_site = config("--site-config", str(site))
    assert from_site["cameras_per_gpu"] == 4
    assert from_site["cameras_per_gpu_source"] == f"site config {site}"
    flag = config("--site-config", str(site), "--cameras-per-gpu", "8")
    assert (flag["cameras_per_gpu"], flag["cameras_per_gpu_source"]) == (8, "--cameras-per-gpu")
    none = config("--site-config", str(tmp_path / "missing.yaml"))
    assert (none["cameras_per_gpu"], none["cameras_per_gpu_source"]) == (1, "default")
    site.write_text("cameras_per_gpu: 0\n", encoding="utf-8")
    bad = cli.invoke(app, [*base, "--out", str(tmp_path / "bad"), "--site-config", str(site)])
    assert bad.exit_code == 2
