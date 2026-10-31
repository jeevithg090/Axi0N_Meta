"""`rulecam owner set-passphrase`: prompts without echo and prints only the hash line."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from rulecam.api.passphrase import PassphraseHash
from rulecam.cli import app

PASSPHRASE = "correct horse battery staple"  # noqa: S105 - a test passphrase


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def answer_with(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[str]:
    asked: list[str] = []
    replies = iter(answers)

    def fake_getpass(prompt: str = "") -> str:
        asked.append(prompt)
        return next(replies)

    monkeypatch.setattr("getpass.getpass", fake_getpass)
    return asked


def test_prints_only_the_hash_line(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = answer_with(monkeypatch, PASSPHRASE, PASSPHRASE)
    result = runner.invoke(app, ["owner", "set-passphrase"])
    assert result.exit_code == 0, result.output
    assert asked == ["New owner passphrase: ", "Repeat it: "]
    assert PASSPHRASE not in result.output
    (line,) = result.output.strip().splitlines()
    key, _, value = line.partition("=")
    assert key == "OWNER_PASSPHRASE_HASH"
    assert PassphraseHash.parse(value).verify(PASSPHRASE)


@pytest.mark.parametrize(
    ("answers", "message"),
    [(("short", "short"), "at least 12"), ((PASSPHRASE, PASSPHRASE + "!"), "differ")],
)
def test_refuses_weak_or_mismatched(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, answers: tuple[str, str], message: str
) -> None:
    answer_with(monkeypatch, *answers)
    result = runner.invoke(app, ["owner", "set-passphrase"])
    assert result.exit_code == 2
    assert message in result.output
    assert "OWNER_PASSPHRASE_HASH" not in result.output
    assert PASSPHRASE not in result.output
