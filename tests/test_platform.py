"""Tests for settings, clocks, logging and the CLI."""

from __future__ import annotations

import json
from datetime import UTC, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.clock import SystemClock
from rulecam.config import Settings
from rulecam.contracts import CONTRACTS_VERSION
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from tests.fakes import EPOCH, FakeClock


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RULECAM_MODE", "live")
    monkeypatch.setenv("META_API_KEY", "secret-value")
    monkeypatch.setenv("RULECAM_DATA_DIR", str(tmp_path))

    settings = Settings(_env_file=None)

    assert settings.rulecam_mode == "live"
    assert settings.meta_api_key is not None
    assert settings.meta_api_key.get_secret_value() == "secret-value"
    assert "secret-value" not in repr(settings)
    assert settings.replay_dir == tmp_path / "replay"


def test_replay_dir_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RULECAM_REPLAY_DIR", "tests/fixtures/replay")
    assert Settings(_env_file=None).replay_dir == Path("tests/fixtures/replay")


def test_settings_default_to_replay_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RULECAM_MODE", raising=False)
    assert Settings(_env_file=None).rulecam_mode == "replay"


def test_system_clock_is_timezone_aware() -> None:
    assert SystemClock().now().tzinfo is UTC


def test_fake_clock_advances() -> None:
    clock = FakeClock()
    clock.advance(1.5)
    assert clock.now() - EPOCH == timedelta(seconds=1.5)


def test_logger_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    get_logger("rulecam.test").info("hello", camera_id="cam1")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(line)
    assert record["event"] == "hello"
    assert record["camera_id"] == "cam1"
    assert record["logger"] == "rulecam.test"


def test_cli_version_reports_contracts_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert CONTRACTS_VERSION in result.output


def test_rulecam_error_is_an_exception() -> None:
    with pytest.raises(RulecamError):
        raise RulecamError("boom")
