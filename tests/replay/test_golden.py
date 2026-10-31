"""Tests for golden runs (P3-02)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache, cached_call, configure
from rulecam.replay.errors import ScenarioError
from rulecam.replay.golden import STEP_REGISTRY, GoldenRunner, StepFn, load_scenario, register_step
from tests.fakes import FakeClock

SCENARIO = """
name: smoke
description: one model call, one derived value
steps:
  - id: ask
    kind: fake.model
    params: {prompt: "is the sink visible?"}
  - id: count
    kind: fake.count
    params: {items: [a, b, c]}
"""


class FakeModel:
    """A 'model' whose answer can change between record and replay."""

    def __init__(self) -> None:
        self.answer = "yes"
        self.calls = 0

    def step(self, params: Mapping[str, object], cache: ReplayCache) -> object:
        def call() -> Mapping[str, object]:
            self.calls += 1
            return {"answer": self.answer}

        reply = cache.call("fake", "m1", {"prompt": params["prompt"]}, call).response
        return {"answer": reply["answer"], "mode": "n/a"}


def count_step(params: Mapping[str, object], cache: ReplayCache) -> object:
    items = params["items"]
    assert isinstance(items, list)
    return len(items)


@pytest.fixture
def setup(tmp_path: Path) -> tuple[GoldenRunner, FakeModel, ReplayCache]:
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "smoke.yaml").write_text(SCENARIO)
    model = FakeModel()
    cache = ReplayCache(tmp_path / "replay", "replay", clock=FakeClock())
    registry: dict[str, StepFn] = {"fake.model": model.step, "fake.count": count_step}
    return GoldenRunner(cache, scenarios, registry), model, cache


def test_record_writes_expected_outputs(setup: tuple[GoldenRunner, FakeModel, ReplayCache]) -> None:
    runner, model, cache = setup
    path = runner.record("smoke")
    data = json.loads(path.read_text())
    assert data["steps"] == {"ask": {"answer": "yes", "mode": "n/a"}, "count": 3}
    assert model.calls == 1
    assert cache.mode == "replay"  # restored after the run


def test_verify_replays_without_calling(setup: tuple[GoldenRunner, FakeModel, ReplayCache]) -> None:
    runner, model, _ = setup
    runner.record("smoke")
    report = runner.verify("smoke")
    assert report.ok
    assert report.steps == 2
    assert model.calls == 1


def test_verify_detects_changed_output(
    setup: tuple[GoldenRunner, FakeModel, ReplayCache], tmp_path: Path
) -> None:
    runner, _, _ = setup
    runner.record("smoke")
    expected = runner.expected_path("smoke")
    data = json.loads(expected.read_text())
    data["steps"]["count"] = 4
    expected.write_text(json.dumps(data))
    report = runner.verify("smoke")
    assert not report.ok
    assert [d.step_id for d in report.diffs] == ["count"]
    assert report.diffs[0].expected == 4
    assert report.diffs[0].actual == 3


def test_verify_reports_replay_miss(setup: tuple[GoldenRunner, FakeModel, ReplayCache]) -> None:
    runner, _, cache = setup
    runner.record("smoke")
    for entry in (cache.directory / "fake").iterdir():
        entry.unlink()
    report = runner.verify("smoke")
    assert not report.ok
    assert report.diffs[0].error is not None
    assert "no recorded reply" in report.diffs[0].error


def test_verify_without_expected_file_fails(
    setup: tuple[GoldenRunner, FakeModel, ReplayCache],
) -> None:
    runner, _, _ = setup
    with pytest.raises(ScenarioError, match="golden record smoke"):
        runner.verify("smoke")


def test_expected_without_steps_fails(setup: tuple[GoldenRunner, FakeModel, ReplayCache]) -> None:
    runner, _, _ = setup
    runner.expected_path("smoke").write_text("[]")
    with pytest.raises(ScenarioError, match="no `steps`"):
        runner.verify("smoke")


def test_unknown_step_kind_fails_before_any_call(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("name: bad\nsteps:\n  - {id: a, kind: nope}\n")
    runner = GoldenRunner(ReplayCache(tmp_path, "replay", clock=FakeClock()), tmp_path, {})
    with pytest.raises(ScenarioError, match="unknown step kind"):
        runner.record("bad")


def test_invalid_and_duplicate_scenarios(tmp_path: Path) -> None:
    (tmp_path / "empty.yaml").write_text("name: e\nsteps: []\n")
    (tmp_path / "dup.yaml").write_text(
        "name: d\nsteps:\n  - {id: a, kind: k}\n  - {id: a, kind: k}\n"
    )
    with pytest.raises(ScenarioError):
        load_scenario(tmp_path / "empty.yaml")
    with pytest.raises(ScenarioError, match="repeats"):
        load_scenario(tmp_path / "dup.yaml")
    with pytest.raises(ScenarioError):
        load_scenario(tmp_path / "missing.yaml")


def test_register_step_adds_to_default_registry() -> None:
    @register_step("test.registered")
    def step(params: Mapping[str, object], cache: ReplayCache) -> object:
        return 1

    try:
        assert STEP_REGISTRY["test.registered"] is step
    finally:
        del STEP_REGISTRY["test.registered"]


def test_cli_record_and_verify(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "cli.yaml").write_text("name: cli\nsteps:\n  - {id: n, kind: test.cli}\n")
    monkeypatch.setenv("RULECAM_REPLAY_DIR", str(tmp_path / "replay"))
    monkeypatch.setattr("rulecam.cli.get_settings", lambda: Settings(_env_file=None))
    STEP_REGISTRY["test.cli"] = count_step_fixed
    try:
        cli = CliRunner()
        recorded = cli.invoke(app, ["golden", "record", "cli", "--scenarios-dir", str(tmp_path)])
        assert recorded.exit_code == 0, recorded.output
        verified = cli.invoke(app, ["golden", "verify", "cli", "--scenarios-dir", str(tmp_path)])
        assert verified.exit_code == 0, verified.output
        assert "1/1 steps reproduced" in verified.output
        (tmp_path / "cli.expected.json").write_text('{"steps": {"n": 2}}')
        broken = cli.invoke(app, ["golden", "verify", "cli", "--scenarios-dir", str(tmp_path)])
        assert broken.exit_code == 1
        assert "✗ n" in broken.output
    finally:
        del STEP_REGISTRY["test.cli"]


def count_step_fixed(params: Mapping[str, object], cache: ReplayCache) -> object:
    return 1


GLOBAL_SCENARIO = """
name: global
steps:
  - {id: first, kind: global.model, params: {prompt: one}}
  - {id: second, kind: global.model, params: {prompt: two}}
"""


def test_cached_call_steps_follow_the_runner_mode(tmp_path: Path) -> None:
    """`cached_call` inside a step uses the runner's cache, never the env-configured one."""
    (tmp_path / "global.yaml").write_text(GLOBAL_SCENARIO)
    calls: list[str] = []

    def step(params: Mapping[str, object], cache: ReplayCache) -> object:
        prompt = str(params["prompt"])

        def call() -> Mapping[str, object]:
            calls.append(prompt)
            return {"echo": prompt}

        return cached_call("fake", "m1", {"prompt": prompt}, call)["echo"]

    env_cache = ReplayCache(tmp_path / "env", "live", clock=FakeClock())  # e.g. RULECAM_MODE=live
    configure(env_cache)
    try:
        cache = ReplayCache(tmp_path / "replay", "replay", clock=FakeClock())
        runner = GoldenRunner(cache, tmp_path, {"global.model": step})
        runner.record("global")
        assert calls == ["one", "two"]
        assert runner.verify("global").ok
        assert calls == ["one", "two"]  # verify never called the model
        for entry in (tmp_path / "replay" / "fake").iterdir():
            if "one" in entry.read_text():
                entry.unlink()
        report = runner.verify("global")
        assert calls == ["one", "two"]
        assert [d.step_id for d in report.diffs] == ["first"]  # the miss names its step...
        assert report.diffs[0].error is not None
        assert "no recorded reply" in report.diffs[0].error
        from rulecam.replay.cache import get_cache  # noqa: PLC0415

        assert get_cache() is env_cache  # ...later steps still ran, and the global is restored
    finally:
        configure(None)


def test_record_leaves_no_temporary_files(
    setup: tuple[GoldenRunner, FakeModel, ReplayCache],
) -> None:
    runner, _, _ = setup
    path = runner.record("smoke")
    assert sorted(p.name for p in path.parent.iterdir()) == ["smoke.expected.json", "smoke.yaml"]
