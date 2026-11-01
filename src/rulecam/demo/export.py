"""Turn a verified golden fixture into the web UI's REPLAY demo (`rulecam golden export`).

The UI plays `ui/src/fixtures/golden/<name>.json` on `?demo=1` (P3-09's demo switch) exactly as it
plays `/ws`: the recorded `planner.step` frames, the approved plan, then the run's chain records
as `candidate.new`, `verdict.new` and `action.done` frames, in chain order. The bundle also holds
the verified golden outputs (Rule CI, evidence, Tier 1 and the policy's decisions, the eval
score), so the demo page shows only what `rulecam golden verify` proves. Strips and the chain
file are copied under `ui/public/golden/<name>/`, where the verify page reads the chain.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path

from rulecam.contracts.events import EventRecord
from rulecam.demo.errors import FixtureError
from rulecam.demo.fixture import STRIPS_DIR, GoldenFixture
from rulecam.replay.cache import atomic_write_text
from rulecam.replay.golden import Scenario

PUBLIC_PREFIX = "/golden"
VERIFY_PAGE = "/verify/index.html"
_ACTION_FIELDS = ("kind", "role", "gate", "ts", "ok", "detail")


def public_path(name: str, *parts: str) -> str:
    """The URL path of a demo asset, e.g. `/golden/<name>/chain.jsonl`."""
    return "/".join((PUBLIC_PREFIX, name, *parts))


def verify_url(name: str, root: str, n: int) -> str:
    """The verify page URL that recomputes the demo chain in the visitor's browser."""
    return f"{VERIFY_PAGE}?chain={public_path(name, 'chain.jsonl')}&root={root}&n={n}"


def _frame(topic: str, payload: object) -> dict[str, object]:
    return {"topic": topic, "payload": payload}


def _record_frames(record: EventRecord, strips: Mapping[str, list[str]]) -> list[dict[str, object]]:
    payload = record.payload
    if record.kind == "candidate":
        raw = payload.get("candidate")
        candidate = dict(raw) if isinstance(raw, dict) else {}
        candidate["strip_paths"] = strips.get(str(candidate["event_id"]), [])
        return [_frame("candidate.new", candidate)]
    event_id = payload.get("event_id")
    if record.kind == "action":
        action = {key: payload.get(key) for key in _ACTION_FIELDS}
        return [_frame("action.done", {"event_id": event_id, "action": action})]
    if record.kind == "verdict" and "tier" in payload:
        verdict = {k: v for k, v in payload.items() if k != "event_id"}
        used = {
            "model": verdict.get("model_version"),
            "segment": "verify",
            "latency_ms": verdict.get("latency_ms", 0),
        }
        return [
            _frame("relay.model_used", used),
            _frame("verdict.new", {"event_id": event_id, "verdict": verdict}),
        ]
    return []  # decisions are in the bundle's `steps.verify`; the UI has no frame for them


def _strip_urls(fixture: GoldenFixture, name: str, event_ids: list[str]) -> dict[str, list[str]]:
    return {
        event_id: [
            public_path(name, STRIPS_DIR, event_id, p.name) for p in fixture.strips(event_id)
        ]
        for event_id in event_ids
    }


def evidence_of(scenario: Scenario, expected: Mapping[str, object]) -> dict[str, object]:
    """The verified `demo.evidence` output of a scenario.

    Raises:
        FixtureError: If the scenario has no verified `demo.evidence` step.
    """
    found = next((expected.get(s.id) for s in scenario.steps if s.kind == "demo.evidence"), None)
    if not isinstance(found, dict):
        msg = f"scenario {scenario.name} has no verified `demo.evidence` step"
        raise FixtureError(msg)
    return found


def _event_ids(evidence: Mapping[str, object]) -> list[str]:
    ids = evidence.get("event_ids")
    return [str(e) for e in ids] if isinstance(ids, list) else []


def build_bundle(
    fixture: GoldenFixture,
    scenario: Scenario,
    expected: Mapping[str, object],
    *,
    clip: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Assemble the UI bundle of one scenario from its fixture and verified outputs.

    Args:
        fixture: The scenario's fixture.
        scenario: The scenario (its name, description and steps).
        expected: The verified step outputs (`<name>.expected.json` `steps`).
        clip: The eval clip's metadata (name, description, license, source), if known.

    Returns:
        The bundle as JSON-ready data.

    Raises:
        FixtureError: If the expected outputs have no `demo.evidence` step.
    """
    name = scenario.name
    run = fixture.run()
    plan = json.loads((fixture.root / "plan.json").read_text(encoding="utf-8"))
    evidence = evidence_of(scenario, expected)
    records = fixture.run_records()
    strips = _strip_urls(fixture, name, _event_ids(evidence))
    frames: list[dict[str, object]] = [
        _frame("mode.changed", {"mode": "replay", "badge": "REPLAY"})
    ]
    frames += [_frame(f.topic, f.payload) for f in run.bus if f.topic == "planner.step"]
    frames.append(_frame("plan.updated", plan))
    for record in records:
        frames += _record_frames(record, strips)
    return {
        "name": name,
        "description": scenario.description,
        "recorded_at": run.started_at.isoformat(),
        "camera_id": run.camera_id,
        "clip": dict(clip or {}),
        "plan": plan,
        "frames": frames,
        "steps": {s.kind.removeprefix("demo."): expected.get(s.id) for s in scenario.steps},
        "chain_url": public_path(name, "chain.jsonl"),
        "verify_url": verify_url(name, str(evidence["root"]), int(str(evidence["n"]))),
    }


def export_ui(
    fixture: GoldenFixture,
    scenario: Scenario,
    expected: Mapping[str, object],
    *,
    bundle_dir: Path,
    public_dir: Path,
    clip: Mapping[str, object] | None = None,
) -> Path:
    """Write the bundle and copy the chain and strips where the UI serves them.

    Args:
        fixture: The scenario's fixture.
        scenario: The scenario.
        expected: Its verified step outputs.
        bundle_dir: `ui/src/fixtures/golden`.
        public_dir: `ui/public` (assets go under `golden/<name>/`).
        clip: The eval clip's metadata.

    Returns:
        The bundle path.
    """
    bundle = build_bundle(fixture, scenario, expected, clip=clip)
    assets = public_dir / PUBLIC_PREFIX.strip("/") / scenario.name
    if assets.exists():
        shutil.rmtree(assets)
    (assets / STRIPS_DIR).mkdir(parents=True)
    shutil.copyfile(fixture.chain_path, assets / "chain.jsonl")
    for event_id in _event_ids(evidence_of(scenario, expected)):
        target = assets / STRIPS_DIR / event_id
        target.mkdir()
        for frame in fixture.strips(event_id):
            shutil.copyfile(frame, target / frame.name)
    path = bundle_dir / f"{scenario.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return path
