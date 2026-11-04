"""Muse Code writes the check headless; replays need no CLI; Spark fills in and says so."""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.clients.errors import ClientConfigError, ModelResponseError
from rulecam.clients.meta_hosted.muse_code import (
    MuseCodeClient,
    child_env,
    parse_events,
    read_output,
)
from rulecam.extensions.generate import CheckGenerator, CheckSpec, spec_text
from rulecam.replay.cache import Mode, ReplayCache
from tests.fakes import FakeClock, FakeLLM
from tests.fakes.extensions import EXAMPLE_CHECK, EXAMPLE_TESTS, FakeMuseCode

SPEC = CheckSpec(
    request="Alert if a pallet is stacked above the yellow line on the rack",
    concept="pallet",
    lines=("yellow",),
    relation="above",
)


def _cache(tmp_path: Path, mode: Mode = "live") -> ReplayCache:
    return ReplayCache(tmp_path / "replay", mode, clock=FakeClock())


def _client(cache: ReplayCache, runner: FakeMuseCode, installed: bool = True) -> MuseCodeClient:
    return MuseCodeClient(
        cache=cache,
        runner=runner,
        which=lambda name: f"/usr/local/bin/{name}" if installed else None,
    )


def test_muse_code_writes_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("META_API_KEY", "secret")
    monkeypatch.setenv("MUSE_PROFILE", "ci")
    runner = FakeMuseCode()
    source = CheckGenerator(_client(_cache(tmp_path), runner)).generate(SPEC)
    assert source.generator == "muse-code"
    assert source.model == "muse-spark-1.2"
    assert source.code == EXAMPLE_CHECK
    assert source.tests == EXAMPLE_TESTS
    [(argv, env, seen)] = runner.calls
    assert argv[:5] == ["/usr/local/bin/muse", "exec", "--json", "--model", "muse-spark-1.2"]
    assert seen == {"SPEC.md", "check.py", "test_check.py"}
    assert "META_API_KEY" not in env
    assert env["MUSE_PROFILE"] == "ci"


def test_recorded_generation_replays_without_the_cli(tmp_path: Path) -> None:
    runner = FakeMuseCode()
    CheckGenerator(_client(_cache(tmp_path, "record"), runner)).generate(SPEC)
    replay = CheckGenerator(_client(_cache(tmp_path, "replay"), runner, installed=False))
    source = replay.generate(SPEC)
    assert source.generator == "muse-code"
    assert source.code == EXAMPLE_CHECK
    assert len(runner.calls) == 1


def test_spark_fills_in_when_the_cli_is_missing(tmp_path: Path) -> None:
    spark = FakeLLM([{"check_py": EXAMPLE_CHECK, "test_check_py": EXAMPLE_TESTS}])
    client = _client(_cache(tmp_path), FakeMuseCode(), installed=False)
    source = CheckGenerator(client, spark).generate(SPEC, variant="measure from the box centre")
    assert source.generator == "spark-fallback"
    assert source.model == "muse-spark-1.3"
    [call] = spark.calls
    messages = call["messages"]
    assert isinstance(messages, list)
    assert "measure from the box centre" in str(messages[-1])


def test_no_generator_at_all_is_a_config_error(tmp_path: Path) -> None:
    client = _client(_cache(tmp_path), FakeMuseCode(), installed=False)
    with pytest.raises(ClientConfigError):
        CheckGenerator(client).generate(SPEC)
    with pytest.raises(ClientConfigError):
        CheckGenerator(None).generate(SPEC)


def test_a_failed_run_is_a_model_error(tmp_path: Path) -> None:
    with pytest.raises(ModelResponseError, match=r"exited with 2"):
        CheckGenerator(_client(_cache(tmp_path), FakeMuseCode(returncode=2))).generate(SPEC)


def test_an_untouched_stub_is_a_model_error(tmp_path: Path) -> None:
    runner = FakeMuseCode(files={})
    with pytest.raises(ModelResponseError, match=r"did not write check\.py"):
        CheckGenerator(_client(_cache(tmp_path), runner)).generate(SPEC)


def test_spec_names_the_request_rules_and_variant() -> None:
    text = spec_text(SPEC, variant="a second way")
    assert SPEC.request in text
    assert "**above** the line" in text
    assert "`yellow`" in text
    assert "only import allowed is `import math`" in text
    assert "a second way" in text
    assert "120 characters" in text


def test_events_parse_leniently() -> None:
    stdout = '{"type": "start"}\n[1]\nnope\n{"type": "message", "text": " done "}\n'
    assert parse_events(stdout) == (2, "done")


def test_outputs_cannot_leave_the_workspace(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("x", encoding="utf-8")
    assert read_output(tmp_path, "ok.py") == "x"
    with pytest.raises(ModelResponseError):
        read_output(tmp_path, "../outside.py")
    with pytest.raises(ModelResponseError):
        read_output(tmp_path, "missing.py")


def test_child_env_keeps_only_safe_keys() -> None:
    env = child_env({"PATH": "/bin", "HOME": "/h", "AWS_SECRET": "x", "MUSE_X": "1"})
    assert env == {"PATH": "/bin", "HOME": "/h", "MUSE_X": "1"}
