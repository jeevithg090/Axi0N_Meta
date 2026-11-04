"""`rulecam extension`: list, and a clear exit when no generator is available."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rulecam.cli import _line, app
from rulecam.config import get_settings


@pytest.fixture
def no_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RULECAM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RULECAM_MUSE_CODE_BIN", "no-such-muse-binary")
    monkeypatch.setenv("RULECAM_MODE", "live")
    monkeypatch.delenv("META_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env file here
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("no_keys")
def test_list_is_empty_and_new_exits_without_a_generator() -> None:
    runner = CliRunner()
    listed = runner.invoke(app, ["extension", "list"])
    assert listed.exit_code == 0, listed.output
    assert "ext:" not in listed.output
    made = runner.invoke(
        app,
        ["extension", "new", "Alert if a pallet is above the line", "--concept", "pallet"],
    )
    assert made.exit_code == 2
    assert "no generator" in made.output


def test_lines_parse() -> None:
    assert _line("0,200,640,210") == ((0.0, 200.0), (640.0, 210.0))
    with pytest.raises(Exception, match="x1,y1,x2,y2"):
        _line("1,2,3")
