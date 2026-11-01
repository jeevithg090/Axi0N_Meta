"""`rulecam eval label`: the consent gate and the list of pairs still to label (task P4-06)."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from rulecam.cli import app

runner = CliRunner()
USAGE_ERROR = 2


def clip_yaml(path: Path, name: str, *, status: str = "negative") -> None:
    """A clip.yaml with one rule. `negative` is a ready label that needs no events."""
    path.mkdir(parents=True, exist_ok=True)
    body = {
        "name": name,
        "camera_id": "cam1",
        "placeholder": True,
        "rules": [{"id": "forklift_2m", "request": "Keep people 2 m from a forklift.",
                   "status": status}],
    }  # fmt: skip
    (path / "clip.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def manifest_yaml(
    path: Path, *, consent: bool, sha256: str = "0" * 64, label: str = "negative"
) -> None:
    body = {
        "name": "e1-cli",
        "entries": [
            {
                "path": "eval/clips/clip_a/clip.mp4",
                "sha256": sha256,
                "use_case": "uc1",
                "rule_id": "forklift_2m",
                "label": label,
                "staged_by": "team",
                "provenance": "recorded",
                "consent": consent,
                "consent_form": "form-001" if consent else "",
            }
        ],
    }
    path.write_text(yaml.safe_dump(body), encoding="utf-8")


def test_a_manifest_without_consent_exits_with_a_usage_error(tmp_path: Path) -> None:
    clips = tmp_path / "eval" / "clips"
    clip_yaml(clips / "clip_a", "clip_a")
    manifest = tmp_path / "m.yaml"
    manifest_yaml(manifest, consent=False)
    result = runner.invoke(app, ["eval", "label", str(manifest), "--set", str(clips)])
    assert result.exit_code == USAGE_ERROR
    assert "no signed consent form" in result.output


def test_a_consented_manifest_reports_the_labelled_pairs(tmp_path: Path) -> None:
    clips = tmp_path / "eval" / "clips"
    clip_yaml(clips / "clip_a", "clip_a")
    manifest = tmp_path / "m.yaml"
    manifest_yaml(manifest, consent=True)
    result = runner.invoke(app, ["eval", "label", str(manifest), "--set", str(clips)])
    assert result.exit_code == 0
    assert "e1-cli: 1 consented clip/rule pairs" in result.output
    assert "every pair is labelled" in result.output


def test_an_unlabelled_pair_is_listed(tmp_path: Path) -> None:
    clips = tmp_path / "eval" / "clips"
    clip_yaml(clips / "clip_a", "clip_a", status="unlabelled")
    manifest = tmp_path / "m.yaml"
    manifest_yaml(manifest, consent=True)
    result = runner.invoke(app, ["eval", "label", str(manifest), "--set", str(clips)])
    assert result.exit_code == 0
    assert "to label: clip_a/forklift_2m" in result.output
    assert "1 still to label" in result.output


def test_a_clip_outside_the_eval_set_is_refused(tmp_path: Path) -> None:
    clips = tmp_path / "eval" / "clips"
    clip_yaml(clips / "other", "other")
    manifest = tmp_path / "m.yaml"
    manifest_yaml(manifest, consent=True)
    result = runner.invoke(app, ["eval", "label", str(manifest), "--set", str(clips)])
    assert result.exit_code == USAGE_ERROR
    assert "not in the eval set" in result.output


def test_a_label_that_contradicts_clip_yaml_is_refused(tmp_path: Path) -> None:
    clips = tmp_path / "eval" / "clips"
    clip_yaml(clips / "clip_a", "clip_a", status="negative")
    manifest = tmp_path / "m.yaml"
    manifest_yaml(manifest, consent=True, label="positive")
    result = runner.invoke(app, ["eval", "label", str(manifest), "--set", str(clips)])
    assert result.exit_code == USAGE_ERROR
    assert "needs its manifest entry reviewed" in result.output


def test_the_installed_cli_runs_without_the_repository_on_the_path(tmp_path: Path) -> None:
    """`rulecam eval label` must work from an installed package, not only from the repo root.

    `-I` drops the working directory and PYTHONPATH from `sys.path`, and the command runs from
    a temporary folder, so an import of the top-level `eval/` folder would fail here.
    """
    clips = tmp_path / "eval" / "clips"
    clip_yaml(clips / "clip_a", "clip_a")
    manifest = tmp_path / "m.yaml"
    manifest_yaml(manifest, consent=False)
    command = "from rulecam.cli import app; app()"
    args = ["eval", "label", str(manifest), "--set", str(clips)]
    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-I", "-c", command, *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert "ModuleNotFoundError" not in result.stderr
    assert result.returncode == USAGE_ERROR
    assert "no signed consent form" in result.stderr


def test_check_hashes_catches_a_re_recorded_clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-recording has a new hash, so the old consent entry must not cover it."""
    clips = tmp_path / "eval" / "clips"
    clip_yaml(clips / "clip_a", "clip_a")
    (clips / "clip_a" / "clip.mp4").write_bytes(b"first cut")
    manifest = tmp_path / "m.yaml"
    manifest_yaml(manifest, consent=True, sha256=hashlib.sha256(b"first cut").hexdigest())
    monkeypatch.chdir(tmp_path)  # `--check-hashes` resolves paths from the repository root

    args = ["eval", "label", str(manifest), "--set", str(clips), "--check-hashes"]
    assert runner.invoke(app, args).exit_code == 0

    (clips / "clip_a" / "clip.mp4").write_bytes(b"re-recorded")
    changed = runner.invoke(app, args)
    assert changed.exit_code == USAGE_ERROR
    assert "needs its own consent entry" in changed.output
