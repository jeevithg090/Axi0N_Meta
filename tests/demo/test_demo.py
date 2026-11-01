"""Golden demo runs: capture, the `demo.*` golden steps, the UI export and the CLI (no network).

The committed `scenarios/warehouse_driver/` fixture (a live record run) is the test data: capture
is checked by rebuilding it from the same files laid out as the API pod has them. Since the
operator warm-up (P1-07) that run is a clean negative with no candidate, so the candidate
mechanics (strips, the Tier 1 reply, the decision, the UI's candidate frames) run on
`tests.demo.synthetic`: the same run plus one pedestrian candidate appended as the runtime does.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.contracts import Track
from rulecam.demo.capture import CaptureSources, capture
from rulecam.demo.errors import CaptureError, FixtureError
from rulecam.demo.export import build_bundle, evidence_of, export_ui, verify_url
from rulecam.demo.fixture import GoldenFixture, RecordedWindows, TrackWindows
from rulecam.demo.steps import fixture_of, planner_steps, verify_step
from rulecam.replay.cache import ReplayCache
from rulecam.replay.golden import GoldenRunner, load_scenario
from tests.demo import synthetic

REPO = Path(__file__).resolve().parents[2]
SCENARIOS = REPO / "scenarios"
NAME = "warehouse_driver"
FIXTURE = SCENARIOS / NAME
EVAL_SET = REPO / "eval" / "clips"
cli = CliRunner()


def scenario_copy(tmp_path: Path, *, with_candidate: bool = False) -> Path:
    """Copy the scenario to `tmp_path/scenarios`, with absolute fixture and eval-set paths.

    With `with_candidate`, the fixture is `tests.demo.synthetic` (the committed run plus one
    pedestrian candidate) and its expected outputs are recorded from it.
    """
    out = tmp_path / "scenarios"
    if with_candidate:
        synthetic.build(FIXTURE, out / NAME)
    else:
        shutil.copytree(FIXTURE, out / NAME)
        shutil.copyfile(SCENARIOS / f"{NAME}.expected.json", out / f"{NAME}.expected.json")
    data = yaml.safe_load((SCENARIOS / f"{NAME}.yaml").read_text(encoding="utf-8"))
    for step in data["steps"]:
        step["params"]["fixture"] = str(out / NAME)
        if "eval_set" in step["params"]:
            step["params"]["eval_set"] = str(EVAL_SET)
    (out / f"{NAME}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    if with_candidate:
        runner(out).record(NAME)
    return out


def runner(scenarios: Path) -> GoldenRunner:
    import rulecam.demo.steps  # noqa: F401, PLC0415 - registers the step kinds

    cache = ReplayCache(scenarios / "no-replies", "replay", clock=FixedClock())
    return GoldenRunner(cache, scenarios)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 11, 1, tzinfo=UTC)


def test_the_committed_golden_run_reproduces(tmp_path: Path) -> None:
    report = runner(scenario_copy(tmp_path)).verify(NAME)
    assert report.ok, report.diffs


def test_record_writes_the_same_outputs_without_any_model(tmp_path: Path) -> None:
    scenarios = scenario_copy(tmp_path)
    expected = json.loads((scenarios / f"{NAME}.expected.json").read_text(encoding="utf-8"))
    (scenarios / f"{NAME}.expected.json").unlink()
    runner(scenarios).record(NAME)  # record mode: the steps still only read the fixture
    again = json.loads((scenarios / f"{NAME}.expected.json").read_text(encoding="utf-8"))
    assert again == expected


def test_what_the_golden_run_proves() -> None:
    """The driver-only clip: the seated driver is the forklift's operator, nothing fires (P1-07)."""
    steps = json.loads((SCENARIOS / f"{NAME}.expected.json").read_text(encoding="utf-8"))["steps"]
    assert steps["plan"]["rule_hashes_match_run"]
    assert [s["step"] for s in steps["plan"]["planner_steps"]][:2] == ["understand", "survey"]
    (ci,) = steps["rule_ci"]
    assert ci["ci_green"]
    assert ci["matches_plan"]
    assert ci["summary"].endswith("mutations 6/6")  # with `drop_operator_warmup`, killed
    assert steps["evidence"]["ok"]
    assert steps["evidence"]["candidates"] == 0
    assert steps["verify"] == []
    assert steps["score"]["status"] == "negative"
    assert steps["score"]["false_alerts_on_negative"] == 0


def test_the_candidate_mechanics_reproduce(tmp_path: Path) -> None:
    scenarios = scenario_copy(tmp_path, with_candidate=True)
    report = runner(scenarios).verify(NAME)
    assert report.ok, report.diffs
    steps = json.loads((scenarios / f"{NAME}.expected.json").read_text(encoding="utf-8"))["steps"]
    assert steps["evidence"]["candidates"] == steps["evidence"]["reproduced"] == 1
    (verified,) = steps["verify"]
    assert verified["tier1"]["scene_text"] == synthetic.SIGN  # our band text never reaches it
    assert verified["tier1_matches_chain"]
    assert verified["decision_matches_chain"]
    assert verified["strip_frames"] == 2


def test_a_changed_reply_or_record_is_caught(tmp_path: Path) -> None:
    scenarios = scenario_copy(tmp_path, with_candidate=True)
    (reply,) = (scenarios / NAME / "replay" / "glimmer").glob("*.json")
    reply.write_text(reply.read_text(encoding="utf-8").replace('\\"violation', '\\"unsure'))
    chain = scenarios / NAME / "chain.jsonl"
    lines = chain.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"ts":"2026-11-01T', '"ts":"2026-11-02T', 1)  # an edited record
    chain.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = runner(scenarios).verify(NAME)
    assert {d.step_id for d in report.diffs} >= {"verify", "evidence"}


def test_a_missing_reply_is_a_replay_miss(tmp_path: Path) -> None:
    scenarios = scenario_copy(tmp_path, with_candidate=True)
    shutil.rmtree(scenarios / NAME / "replay")
    report = runner(scenarios).verify(NAME)
    (diff,) = [d for d in report.diffs if d.step_id == "verify"]
    assert diff.error is not None


def test_tier2_or_the_firewall_cannot_be_replayed_yet(tmp_path: Path) -> None:
    scenarios = scenario_copy(tmp_path)
    response = scenarios / NAME / "response.json"
    data = json.loads(response.read_text(encoding="utf-8"))
    response.write_text(json.dumps({**data, "tier2": "configured"}), encoding="utf-8")
    with pytest.raises(FixtureError, match="Tier 2"):
        verify_step(
            {"fixture": str(scenarios / NAME)}, ReplayCache(tmp_path, "replay", clock=FixedClock())
        )


def test_fixture_parameter_and_files_are_checked(tmp_path: Path) -> None:
    with pytest.raises(FixtureError, match="fixture"):
        fixture_of({})
    with pytest.raises(FixtureError, match="fixture"):
        fixture_of({"fixture": str(tmp_path / "missing")})
    empty = GoldenFixture(tmp_path)
    for read in (empty.run, empty.plan, empty.response, empty.chain, empty.windows):
        with pytest.raises(FixtureError):
            read()
    for name in ("run.json", "plan.json", "response.json", "windows.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    for read in (empty.run, empty.plan, empty.response):
        with pytest.raises(FixtureError):
            read()
    (tmp_path / "windows.json").write_text('{"windows": 3}', encoding="utf-8")
    with pytest.raises(FixtureError):
        empty.windows()
    assert empty.strips("nothing") == []


def test_recorded_windows_answer_only_what_was_recorded(tmp_path: Path) -> None:
    root = scenario_copy(tmp_path, with_candidate=True) / NAME
    fixture = GoldenFixture(root)
    data = TrackWindows.model_validate_json((root / "windows.json").read_text())
    (window,) = data.windows
    store = RecordedWindows(data)
    assert store.window(window.camera_id, window.t0, window.t1) == list(window.tracks)
    assert store.window("other", window.t0, window.t1) == []
    with pytest.raises(FixtureError, match="read-only"):
        store.append([])
    candidate: Any = fixture.run_records()[0].payload["candidate"]
    assert [p.name for p in fixture.strips(candidate["event_id"])] == ["1.jpg", "2.jpg"]
    assert planner_steps(fixture)[-1]["step"] == "prove"


def pod_layout(tmp_path: Path, root: Path | None = None) -> CaptureSources:
    """A fixture's files laid out as the API pod keeps them (default: the synthetic one)."""
    if root is None:
        root = scenario_copy(tmp_path / "source", with_candidate=True) / NAME
    fixture = GoldenFixture(root)
    run = fixture.run()
    strips = tmp_path / "pod" / "strips"
    strips.mkdir(parents=True)
    for event in (root / "strips").iterdir() if (root / "strips").is_dir() else ():
        shutil.copytree(event, strips / run.camera_id / event.name)
    metrics = tmp_path / "pod" / "metrics.json"
    metrics.write_text(
        json.dumps({"lag_s": 0, "response": json.loads((root / "response.json").read_text())}),
        encoding="utf-8",
    )
    return CaptureSources(
        run=root / "run.json",
        plan=root / "plan.json",
        response=metrics,
        chain=root / "chain.jsonl",
        tracks=fixture.windows(),
        strips=strips,
        replay=root / "replay",
    )


@pytest.mark.parametrize("committed", [True, False], ids=["committed", "with-candidate"])
def test_capture_rebuilds_the_fixture_byte_for_byte(tmp_path: Path, committed: bool) -> None:
    sources = pod_layout(tmp_path, FIXTURE if committed else None)
    root = sources.run.parent
    out = tmp_path / "captured"
    capture(sources, out)
    expected = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    captured = sorted(p.relative_to(out) for p in out.rglob("*") if p.is_file())
    assert captured == expected
    if not committed:
        assert any(p.name == "annotations.json" for p in captured)  # what the bands say
    for rel in expected:
        if rel.suffix == ".json":  # the committed copies end with a newline (pre-commit)
            assert json.loads((out / rel).read_text()) == json.loads((root / rel).read_text())
        else:
            assert (out / rel).read_bytes() == (root / rel).read_bytes(), rel


@pytest.mark.parametrize("broken", ["replay", "strips", "tracks", "metrics", "plan"])
def test_capture_refuses_an_incomplete_run(tmp_path: Path, broken: str) -> None:
    sources = pod_layout(tmp_path)
    if broken == "replay":
        sources = CaptureSources(**{**sources.__dict__, "replay": tmp_path / "none"})
    elif broken == "strips":
        shutil.rmtree(sources.strips)
    elif broken == "tracks":
        sources = CaptureSources(**{**sources.__dict__, "tracks": RecordedWindows(TrackWindows())})
    elif broken == "metrics":
        sources.response.write_text("{}", encoding="utf-8")
    else:
        sources = CaptureSources(**{**sources.__dict__, "plan": tmp_path / "missing.json"})
    with pytest.raises(CaptureError):
        capture(sources, tmp_path / "out")


def test_the_ui_bundle_replays_the_run_in_chain_order(tmp_path: Path) -> None:
    scenarios = scenario_copy(tmp_path / "source", with_candidate=True)
    scenario = load_scenario(scenarios / f"{NAME}.yaml")
    expected = json.loads((scenarios / f"{NAME}.expected.json").read_text())["steps"]
    root = scenarios / NAME
    fixture = GoldenFixture(root)
    bundle: Any = build_bundle(fixture, scenario, expected, clip={"name": "warehouse_45848"})
    topics = [f["topic"] for f in bundle["frames"]]
    assert topics[0] == "mode.changed"
    assert (
        topics.index("plan.updated") < topics.index("candidate.new") < topics.index("verdict.new")
    )
    assert topics.index("relay.model_used") == topics.index("verdict.new") - 1
    candidate = next(f for f in bundle["frames"] if f["topic"] == "candidate.new")
    assert all(p.startswith(f"/golden/{NAME}/strips/") for p in candidate["payload"]["strip_paths"])
    evidence: Any = evidence_of(scenario, expected)
    assert bundle["verify_url"] == verify_url(NAME, str(evidence["root"]), int(evidence["n"]))
    with pytest.raises(FixtureError, match=r"demo\.evidence"):
        evidence_of(scenario, {})
    path = export_ui(
        fixture,
        scenario,
        expected,
        bundle_dir=tmp_path / "bundle",
        public_dir=tmp_path / "public",
        clip={"name": "warehouse_45848"},
    )
    assert json.loads(path.read_text()) == json.loads(json.dumps(bundle))
    assets = tmp_path / "public" / "golden" / NAME
    assert (assets / "chain.jsonl").read_bytes() == (root / "chain.jsonl").read_bytes()
    assert len(list(assets.glob("strips/*/*.jpg"))) == 2


def test_the_committed_ui_copies_match_the_fixture() -> None:
    """The site serves copies: they must be the verified fixture's bytes."""
    ui = REPO / "ui"
    assets = ui / "public" / "golden" / NAME
    assert (assets / "chain.jsonl").read_bytes() == (FIXTURE / "chain.jsonl").read_bytes()
    for strip in (FIXTURE / "strips").rglob("*.jpg"):
        assert (assets / strip.relative_to(FIXTURE)).read_bytes() == strip.read_bytes()
    bundle = json.loads((ui / "src" / "fixtures" / "golden" / f"{NAME}.json").read_text())
    expected = json.loads((SCENARIOS / f"{NAME}.expected.json").read_text())["steps"]
    assert bundle["steps"] == {k: expected[k] for k in bundle["steps"]}
    verify_page = REPO / "src" / "rulecam" / "evidence" / "verify_page"
    for name in ("index.html", "chain.js"):
        assert (ui / "public" / "verify" / name).read_bytes() == (verify_page / name).read_bytes()


def test_cli_capture_and_export(tmp_path: Path) -> None:
    sources = pod_layout(tmp_path)
    data = tmp_path / "pod"
    store = data / "tracks"
    store.mkdir()
    root = sources.run.parent
    windows = TrackWindows.model_validate_json((root / "windows.json").read_text())
    tracks: list[Track] = [t for w in windows.windows for t in w.tracks]
    from rulecam.rules.store import ParquetTrackStore  # noqa: PLC0415

    with ParquetTrackStore(store) as parquet:
        parquet.append(tracks)
    shutil.copytree(root / "replay", data / "replay")
    scenarios = tmp_path / "scenarios"
    result = cli.invoke(
        app,
        [
            "golden", "capture", NAME,
            "--run", str(sources.run), "--plan", str(sources.plan),
            "--metrics", str(sources.response), "--chain", str(sources.chain),
            "--data-dir", str(data), "--scenarios-dir", str(scenarios),
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert (scenarios / NAME / "windows.json").is_file()
    bad = cli.invoke(
        app,
        [
            "golden", "capture", NAME,
            "--run", str(sources.run), "--plan", str(sources.plan),
            "--metrics", str(sources.response), "--chain", str(sources.chain),
            "--data-dir", str(tmp_path / "empty"), "--scenarios-dir", str(tmp_path / "x"),
        ],
    )  # fmt: skip
    assert bad.exit_code == 1

    copied = scenario_copy(tmp_path / "export", with_candidate=True)
    ui = tmp_path / "ui"
    result = cli.invoke(
        app,
        ["golden", "export", NAME, "--ui-dir", str(ui), "--set", str(EVAL_SET),
         "--scenarios-dir", str(copied)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    bundle = json.loads((ui / "src" / "fixtures" / "golden" / f"{NAME}.json").read_text())
    assert bundle["clip"]["name"] == "warehouse_45848"
    (copied / NAME / "replay" / "glimmer").rename(copied / NAME / "replay" / "gone")
    refused = cli.invoke(
        app, ["golden", "export", NAME, "--ui-dir", str(ui), "--scenarios-dir", str(copied)]
    )
    assert refused.exit_code == 1
