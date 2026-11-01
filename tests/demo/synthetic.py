"""A golden fixture with a candidate, built offline from the committed run (tests only).

The committed `warehouse_driver` run is a clean negative since the operator warm-up (P1-07): no
candidate, so no strip, no Tier 1 reply. The capture, verify and export mechanics are therefore
tested on this fixture: the committed run's plan, run record and day chain, plus one pedestrian
candidate appended exactly as the runtime appends it (candidate, Glimmer verdict, decision), its
track window, a two-frame strip with `annotations.json`, and a Glimmer reply recorded through the
replay cache by a fake transport. Glimmer "copies" our band text and a real sign into
`scene_text`; the chained verdict keeps only the sign.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from rulecam.clients.open_weights.glimmer import PROVIDER, GlimmerClient, GlimmerConfig
from rulecam.contracts import Calibration, Track, TrackPoint
from rulecam.demo.fixture import (
    CHAIN_FILE,
    PLAN_FILE,
    RESPONSE_FILE,
    RUN_FILE,
    STRIPS_DIR,
    WINDOWS_FILE,
    GoldenFixture,
    TrackWindow,
    TrackWindows,
)
from rulecam.evidence.chain import EventChain
from rulecam.replay.cache import ReplayCache
from rulecam.rules.backtest import DecisionContext, candidate_payload
from rulecam.verify.annotations import write_annotations
from rulecam.verify.policy import VerificationPolicy
from rulecam.verify.tier1 import GlimmerVerifier

MODEL = "muse-glimmer-30b"
SIGN = "FORKLIFTS ONLY"
DRAWN = ("RULECAM ANNOTATION (not part of the scene)", "subject: person", "forklift")
REPLY = {
    "verdict": "violation",
    "p_violation": 0.91,
    "reason": "A pedestrian walks beside the moving forklift, about half a metre away.",
    "scene_text": f"RULECAM ANNOTATION (not part of the scene)\nsubject: person\n{SIGN}",
}


class _Clock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


def _track(camera_id: str, concept: str, feet: list[tuple[datetime, float]]) -> Track:
    """Boxes above ground feet at y = 500 px; 100 px = 1 m (`calibration`)."""
    track_id = f"{camera_id}:{concept}:1"
    width, height = (200.0, 240.0) if concept == "forklift" else (60.0, 120.0)
    points = tuple(
        TrackPoint(
            ts=ts,
            frame_idx=i,
            box_xyxy=(x - width / 2, 500.0 - height, x + width / 2, 500.0),
            foot_px=(x, 500.0),
        )
        for i, (ts, x) in enumerate(feet)
    )
    return Track(track_id=track_id, camera_id=camera_id, concept=concept, points=points)


def _transport(request: object) -> dict[str, object]:
    content = json.dumps(REPLY)
    message = {"role": "assistant", "content": content}
    return {"status": 200, "headers": {}, "json": {"choices": [{"message": message}]}}


def build(committed: Path, out: Path) -> GoldenFixture:
    """Build the fixture in `out` from the committed fixture folder `committed`."""
    out.mkdir(parents=True)
    for name in (RUN_FILE, PLAN_FILE, RESPONSE_FILE):
        shutil.copyfile(committed / name, out / name)
    fixture = GoldenFixture(out)
    run, plan = fixture.run(), fixture.plan()
    rule = plan.rules[0]
    camera = run.camera_id
    records = GoldenFixture(committed).chain()
    t1 = records[-1].ts + timedelta(seconds=1)
    t0 = t1 - timedelta(seconds=30)
    ticks = [t1 - timedelta(seconds=6) + timedelta(seconds=i / 10) for i in range(60)]
    # Both move at 1 m/s; the person walks 0.5 m beyond the forklift's front edge.
    forklift = _track(camera, "forklift", [(t, 300 + i * 10.0) for i, t in enumerate(ticks)])
    walker = _track(camera, "person", [(t, 450 + i * 10.0) for i, t in enumerate(ticks)])
    calibration = Calibration(
        camera_id=camera,
        homography=((0.01, 0.0, 0.0), (0.0, 0.01, 0.0), (0.0, 0.0, 1.0)),
        method="clicks4",
        err_m=0.01,
    )
    context = DecisionContext(
        rule=rule,
        calibration=calibration,
        t0=t0,
        t1=t1,
        plan_version_hash=run.plan_version_hash or "",
    )
    candidate = context.evaluate([forklift, walker])[0]
    (out / WINDOWS_FILE).write_text(
        TrackWindows(
            windows=(TrackWindow(camera_id=camera, t0=t0, t1=t1, tracks=(forklift, walker)),)
        ).model_dump_json(indent=1),
        encoding="utf-8",
    )
    strip = out / STRIPS_DIR / candidate.event_id
    strip.mkdir(parents=True)
    for k in (1, 2):
        frame = np.full((40, 64, 3), 40 * k, dtype=np.uint8)
        cv2.imwrite(str(strip / f"{k}.jpg"), frame)
    write_annotations(strip, DRAWN)
    shown = candidate.model_copy(
        update={"strip_paths": tuple(str(p) for p in fixture.strips(candidate.event_id))}
    )
    clock = _Clock(t1.astimezone(UTC))
    cache = ReplayCache(fixture.replay_dir, "record", clock=clock)
    client = GlimmerClient(
        cache.wrap_transport(PROVIDER, MODEL, _transport),
        clock=clock,
        config=GlimmerConfig(model=MODEL),
    )
    tier1 = GlimmerVerifier(client, monotonic=lambda: 0.0, raise_errors=True).verify(shown, rule)
    decision = VerificationPolicy(tier1=GlimmerVerifier(client), screen_required=True).decide(
        shown, rule, tier1
    )
    chain_dir = out / "chain-dir"
    day_file = EventChain(chain_dir, records[0].site, clock=clock).path_for(t1.date())
    day_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(committed / CHAIN_FILE, day_file)
    chain = EventChain(chain_dir, records[0].site, clock=clock)
    chain.append("candidate", candidate_payload(candidate, context))
    chain.append("verdict", {"event_id": candidate.event_id, **tier1.model_dump(mode="json")})
    chain.append(
        "verdict", {"event_id": candidate.event_id, "decision": decision.model_dump(mode="json")}
    )
    shutil.copyfile(day_file, out / CHAIN_FILE)
    shutil.rmtree(chain_dir)
    return fixture
