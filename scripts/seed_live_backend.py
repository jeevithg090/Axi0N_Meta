"""Seed a LOCAL Rulecam backend with hand-made SAMPLE data, for developing the cockpit UI.

Everything this script writes is hand-made sample data for UI development, NOT a recorded run:
the plans' probes, calibration and audit findings, every candidate, Glimmer verdict, action,
acknowledgement and dispute outcome were written by hand. Nothing here is a model call or a
measurement, and none of it may be quoted as a result. Each such record says so in its text
("[SAMPLE ...]") and verdicts carry `model_version="sample-seed (no model call)"`, so the UI,
an incident report or a screenshot cannot pass it off as live.

What it writes, all under the repository's runtime folders:

- `ui/public/videos/` and `ui/public/strips/`: copies of stock clips from `data/raw_clips/`
  and three stills per camera (git-ignored videos; the stills are committed as UI samples);
- `data/chunks/index.jsonl`: one chunk per sample camera, so `GET /cameras` lists them;
- `plans/`: the golden `warehouse_driver` plan (a real recording, copied unchanged) and two
  hand-made sample plans (`SAMPLE_PLAN_IDS`; git-ignored, see `samples/seed/`);
- `data/evidence/chain/demo/<today>.jsonl`: a sample chain for today, and the golden 1 Nov
  chain copied to `2026-11-01.jsonl` when that day has no chain yet;
- `data/evidence/sample-roots/`: their Merkle roots. Never `evidence/roots/`: that folder holds
  the committed roots of real chains, and a sample root there would be a false public record.

Safety: today's chain is replaced only when it is one this script wrote (its verdicts carry the
sample marker); a real chain is left alone unless `--force` is given. A real 1 Nov chain is
never overwritten. Run it on a development machine, never on the API pod.

    uv run python scripts/seed_live_backend.py [--force] [--api http://localhost:8000]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import cv2
import httpx
import yaml

from rulecam.contracts import (
    Calibration,
    CandidateEvent,
    Chunk,
    Dispute,
    Measurement,
    PrivacyMode,
    Provenance,
    RequestItem,
    RuleIR,
    Severity,
    SitePlan,
    VerifierVerdict,
)
from rulecam.contracts.plan import (
    AcceptanceCheck,
    AcceptanceResult,
    AuditFinding,
    FeasibilityVerdict,
    InventoryItem,
    Probe,
)
from rulecam.contracts.rule_ir import (
    ActionSpec,
    All,
    ConceptSpec,
    DistanceM,
    Inside,
    Moving,
    RuleSource,
    VerifySpec,
    Without,
    ZoneSpec,
)
from rulecam.evidence.chain import EventChain, verify_chain
from rulecam.evidence.merkle import chain_root
from rulecam.log import get_logger
from rulecam.planner.pipeline import PipelineInput, PlanStore
from rulecam.rules.backtest import DecisionContext, candidate_payload
from rulecam.rules.hashing import rule_ir_hash
from rulecam.runtime.activate import PlanActivator
from rulecam.runtime.loop import ActivePlan
from rulecam.worker.log import dispute_payload, read_site_log

_log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RAW_CLIPS = ROOT / "data" / "raw_clips"
VIDEOS_DIR = ROOT / "ui" / "public" / "videos"
PUBLIC_STRIPS = ROOT / "ui" / "public" / "strips"
DATA_DIR = ROOT / "data"
PLANS_DIR = ROOT / "plans"
SAMPLE_ROOTS_DIR = DATA_DIR / "evidence" / "sample-roots"
GOLDEN_CHAIN = ROOT / "ui" / "public" / "golden" / "warehouse_driver" / "chain.jsonl"
GOLDEN_DAY = "2026-11-01"
SITE = "demo"

# Labels carried by every hand-written record, so no screen or report can show it as real.
SAMPLE = "[SAMPLE: hand-written, not a model call]"
SAMPLE_MODEL = "sample-seed (no model call)"
SEED_MARKER = "sample-seed"
SAMPLE_PLAN_IDS = ("plan-8b21c409a7e1", "plan-c914e72a03f5")
MASTER_PLAN_ID = "plan-c914e72a03f5"
STRIP_FRACTIONS = (0.2, 0.5, 0.8)
HTTP_TIMEOUT_S = 3.0

CAMERA_SPECS = (
    ("CAM-01", "North Loading Dock · Bay 1-3", "warehouse_walk_23550.mp4", "cam01_dock.mp4"),
    ("CAM-02", "Main Aisle B2 Intersection", "engineer_walk_23406.mp4", "cam02_intersection.mp4"),
    ("CAM-03", "East Visitor & PPE Corridor", "warehouse_group_23237.mp4", "cam03_ppe.mp4"),
    (
        "CAM-04",
        "Staging Bay C4 · Forklift Cab",
        "forklift_driver_45848.mp4",
        "cam04_forklift.mp4",
    ),
    ("CAM-05", "Commercial Kitchen · Prep Line A", "kitchen_prep_15875.mp4", "cam05_kitchen.mp4"),
    (
        "CAM-06",
        "West Assembly & Blueprint Zone",
        "warehouse_incident_23238.mp4",
        "cam06_altercation.mp4",
    ),
    ("CAM-08", "South Freight Gate · Truck Bay", "freight_truck_23011.mp4", "cam08_packaging.mp4"),
    (
        "CAM-09",
        "Yard Heavy Equipment · Boom Zone",
        "excavator_swing_49192.mp4",
        "cam09_excavator.mp4",
    ),
    (
        "CAM-10",
        "High-Bay Pallet Racking · Aisle 4",
        "warehouse_pallets_45847.mp4",
        "cam10_pallets.mp4",
    ),
    (
        "CAM-11",
        "Receiving Bay D1 · Forklift Lane",
        "forklift_drive_24127.mp4",
        "cam11_receiving.mp4",
    ),
    ("CAM-12", "North Mezzanine Patrol Walkway", "worker_aisle_23124.mp4", "cam12_corridor.mp4"),
    (
        "phone-27",
        "Paired Phone #27 · Warehouse Floor",
        "forklift_driver_45848.mp4",
        "cam04_forklift.mp4",
    ),
)
ALL_CAMERAS = tuple(spec[0] for spec in CAMERA_SPECS)

HOMOGRAPHY = (
    (0.005486241729051363, 0.0004220185945424167, -4.175029955808087),
    (-3.102079079558648e-19, 0.05522316095296006, 5.908614624992072e-16),
    (-0.00037807183364839365, -0.00046691273871012545, 0.9999999999999999),
)


@dataclass(frozen=True)
class IncidentSpec:
    """One hand-written sample incident: a candidate and what happened to it."""

    event_id: str
    rule_id: str
    camera_id: str
    value: float
    unit: str
    speed: float
    subject: str
    verdict: Literal["violation", "no_violation", "unsure"]
    score: float
    reason: str
    action_kind: str
    action_role: str
    action_detail: str
    ack_role: str | None = None
    dispute: tuple[str, str, Literal["pending", "upheld", "rejected"]] | None = None


INCIDENTS = (
    IncidentSpec(
        event_id="ev-91a4c802d3e177b54012a9c1",
        rule_id="keep-people-2m-from-moving-forklift",
        camera_id="CAM-02",
        value=1.18,
        unit="m",
        speed=1.84,
        subject="CAM-02:person:84a1c02b",
        verdict="violation",
        score=0.96,
        reason=(
            "A pedestrian in a yellow safety vest walks into the Aisle B2 intersection 1.18 m"
            " ahead of a crossing counterbalance forklift moving at 1.84 m/s."
        ),
        action_kind="speak",
        action_role="operator",
        action_detail="would play the proximity horn and notify safety_officer (1.18 m < 2.00 m)",
        ack_role="safety_officer",
    ),
    IncidentSpec(
        event_id="ev-2e3f2c20bf90ae8efa75c8c7",
        rule_id="keep-people-2m-from-moving-forklift",
        camera_id="CAM-04",
        value=0.08,
        unit="m",
        speed=0.62,
        subject="CAM-04:person:fe37c5cd",
        verdict="no_violation",
        score=0.04,
        reason=(
            "The person at 0.08 m is seated in the forklift cab at the controls; no pedestrian"
            " is near the moving forklift."
        ),
        action_kind="notify",
        action_role="safety_officer",
        action_detail="suppressed: Tier 1 said no violation (seated operator in the cab)",
        dispute=(
            "rule_was_followed",
            "Seated operator inside forklift cab, not a pedestrian.",
            "upheld",
        ),
    ),
    IncidentSpec(
        event_id="ev-3d82f109c4b681e29043b7a2",
        rule_id="require-hardhat-in-visitor-corridor",
        camera_id="CAM-03",
        value=3.0,
        unit="persons",
        speed=0.85,
        subject="CAM-03:person:3c90e11a",
        verdict="violation",
        score=0.94,
        reason=(
            "A guide in a red helmet walks three visitors in business clothes through the East"
            " Corridor without hardhats for 6.4 s."
        ),
        action_kind="notify",
        action_role="supervisor",
        action_detail="would notify the floor supervisor: 3 visitors without hardhats (CAM-03)",
        ack_role="supervisor",
    ),
    IncidentSpec(
        event_id="ev-8c45d210e7f392a16094c8b3",
        rule_id="heavy-equipment-swing-radius-exclusion",
        camera_id="CAM-09",
        value=2.14,
        unit="m",
        speed=0.95,
        subject="CAM-09:person:71b4d90e",
        verdict="violation",
        score=0.95,
        reason=(
            "A ground worker comes within 2.14 m of the slewing excavator boom (> 0.15 m/s),"
            " inside the 3.50 m exclusion radius."
        ),
        action_kind="interlock",
        action_role="site_lead",
        action_detail="would trip the Yard Bay 9 swing beacon and notify safety_officer",
        ack_role="site_lead",
    ),
    IncidentSpec(
        event_id="ev-5a92e641b8c043d71029f5e6",
        rule_id="heavy-equipment-swing-radius-exclusion",
        camera_id="CAM-08",
        value=1.62,
        unit="m",
        speed=1.12,
        subject="CAM-08:person:55d2a18f",
        verdict="violation",
        score=0.92,
        reason=(
            "A pedestrian crosses the South Freight Gate apron 1.62 m behind a backing freight"
            " truck moving at 1.12 m/s."
        ),
        action_kind="notify",
        action_role="safety_officer",
        action_detail="would notify safety_officer and play the dock warning on CAM-08",
        ack_role="safety_officer",
    ),
    IncidentSpec(
        event_id="ev-6f01b394a2d819c45081e3f4",
        rule_id="require-hardhat-in-visitor-corridor",
        camera_id="CAM-06",
        value=0.42,
        unit="m",
        speed=1.65,
        subject="CAM-06:person:90e3b71c",
        verdict="violation",
        score=0.91,
        reason="Two workers in the West Assembly Zone (CAM-06) shove each other over a blueprint.",
        action_kind="notify",
        action_role="supervisor",
        action_detail="would escalate a high-priority floor alert to the supervisor (CAM-06)",
        ack_role="supervisor",
    ),
    IncidentSpec(
        event_id="ev-7c178ff2fc3077ff611eb7e9",
        rule_id="commercial-kitchen-prep-sanitation-guard",
        camera_id="CAM-05",
        value=0.12,
        unit="m",
        speed=0.18,
        subject="CAM-05:person:22c8f401",
        verdict="no_violation",
        score=0.11,
        reason="All four cooks on Prep Line A wear white chef hats while plating.",
        action_kind="notify",
        action_role="kitchen_lead",
        action_detail="cleared: Tier 1 said no violation (all head coverings present)",
        dispute=("glare_or_shadow", "Overhead hood shadow briefly hid a white chef cap.", "upheld"),
    ),
)


class _SteppingClock:
    """A clock that moves 2.142 s on every reading, so sample records get distinct times."""

    def __init__(self, start: datetime) -> None:
        self._cur = start

    def now(self) -> datetime:
        """Return the next timestamp."""
        self._cur += timedelta(seconds=2, microseconds=142000)
        return self._cur


class _RecordingRuntime:
    """Stands in for the runtime loop: `PlanActivator` hands it the active plan."""

    def __init__(self) -> None:
        self.plan: ActivePlan | None = None

    def set_plan(self, active: ActivePlan) -> None:
        """Keep the plan the activator hands over."""
        self.plan = active


def _sample_calibration(camera_id: str) -> Calibration:
    return Calibration(
        camera_id=camera_id,
        homography=HOMOGRAPHY,
        method="reference_object",
        reference="pallet_48x40in",
        err_m=0.094,
    )


# ---------------------------------------------------------------- videos, stills and chunks


def _extract_stills(cam_id: str, path: Path, strips_dir: Path, frames_dir: Path) -> None:
    """Write three stills of one clip (20%, 50%, 80% in) to the data and public folders."""
    cap = cv2.VideoCapture(str(path))
    total = max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 1)
    for idx, frac in enumerate(STRIP_FRACTIONS):
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(int(total * frac), total - 1))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        strip_file = strips_dir / f"{cam_id}_strip_{idx}.jpg"
        cv2.imwrite(str(strip_file), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        shutil.copy2(strip_file, PUBLIC_STRIPS / strip_file.name)
        if idx == 1:
            cv2.imwrite(str(frames_dir / f"{cam_id}.jpg"), frame)
    cap.release()


def prepare_videos_and_strips() -> dict[str, Path]:
    """Copy the stock clips into `ui/public/videos/` and extract three stills per camera.

    Returns:
        Each sample camera's clip path (the public copy when it exists).
    """
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_STRIPS.mkdir(parents=True, exist_ok=True)
    strips_dir = DATA_DIR / "strips"
    frames_dir = DATA_DIR / "planner_frames"
    strips_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    cam_paths: dict[str, Path] = {}
    for cam_id, _label, src_name, dst_name in CAMERA_SPECS:
        src, dst = RAW_CLIPS / src_name, VIDEOS_DIR / dst_name
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)
        cam_paths[cam_id] = dst if dst.is_file() else src

    for cam_id, path in cam_paths.items():
        done = all((strips_dir / f"{cam_id}_strip_{i}.jpg").is_file() for i in range(3))
        if path.is_file() and not done:
            _extract_stills(cam_id, path, strips_dir, frames_dir)
    return cam_paths


def _chunk_for(index: int, cam_id: str, path: Path, now: datetime) -> Chunk:
    cap = cv2.VideoCapture(str(path))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    n_frames = max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 150), 1)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
    cap.release()
    return Chunk(
        chunk_id=f"chk-{cam_id.lower()}-{index:03d}",
        camera_id=cam_id,
        path=str(path.resolve()),
        start_ts=now - timedelta(seconds=12 - (index % 9)),
        fps=fps,
        n_frames=n_frames,
        width=width,
        height=height,
        provenance=Provenance.RECORDED,
    )


def seed_chunks(cam_paths: dict[str, Path], now: datetime) -> None:
    """Write `data/chunks/index.jsonl` so `GET /cameras` lists every sample camera.

    Args:
        cam_paths: Each camera's clip, from `prepare_videos_and_strips`.
        now: The time the chunks are dated near.
    """
    chunks_dir = DATA_DIR / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        _chunk_for(i, cam_id, cam_paths[cam_id], now).model_dump_json()
        for i, (cam_id, _label, _src, _dst) in enumerate(CAMERA_SPECS)
        if cam_paths[cam_id].is_file()
    ]
    (chunks_dir / "index.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- sample plans


def _rule_checks(rule_id: str, concern_text: str, standard_detail: str) -> list[AcceptanceCheck]:
    """The nine acceptance checks of one rule, all passing and all labelled sample."""
    checks = (
        ("concern_covered", concern_text, f"rule '{rule_id}'"),
        ("zone_in_view", rule_id, "all monitored zones in the camera frame"),
        ("detect_lb>=0.8", rule_id, "lowest detect_lb 0.89 (24/24 probe frames)"),
        ("calibration_err<=tol", rule_id, "calibrated within 0.094 m (tol 0.10 m)"),
        ("privacy_consistent", rule_id, "standard mode (face blur, non-biometric tracks)"),
        ("citation_text_match", rule_id, standard_detail),
        ("no_private_spaces", rule_id, "no private-space zones"),
        ("ci_green", rule_id, "boundary 16/16, mutations 6/6"),
        ("nonvacuous", rule_id, "fires on its positive boundary trace, not on its negative one"),
    )
    return [
        AcceptanceCheck(check=check, item=item, passed=True, detail=f"{SAMPLE} {detail}")
        for check, item, detail in checks
    ]


def _zones(prefix: str, cameras: Sequence[str], box: tuple[float, ...]) -> dict[str, ZoneSpec]:
    """One polygon zone per camera, named `<prefix>_<camera>` (box: x0, y0, x1, y1, x2, x3)."""
    x0, y0, x1, y1, x2, x3 = box
    return {
        f"{prefix}_{cam.lower().replace('-', '_')}": ZoneSpec(
            camera_id=cam,
            kind="polygon",
            polygon_px=((x0, y0), (x1, y0), (x2, y1), (x3, y1)),
        )
        for cam in cameras
    }


def _source(text: str, plan_id: str, *standards: str) -> RuleSource:
    return RuleSource(kind="text", text=text, lang="en", plan_id=plan_id, standard=standards)


def _verify(min_confidence: float, *, tier2: bool = True) -> VerifySpec:
    return VerifySpec(
        tier1="glimmer",
        tier2="spark-open-on-disagree" if tier2 else "none",
        min_confidence=min_confidence,
    )


def _forklift_rule(plan_id: str) -> RuleIR:
    cameras = ("CAM-01", "CAM-02", "CAM-04", "CAM-10", "CAM-11", "phone-27")
    return RuleIR(
        id="keep-people-2m-from-moving-forklift",
        version=3,
        source=_source(
            "Keep people at least 2 metres away from a moving forklift.",
            plan_id,
            "OSHA-1910.178(n)(1)",
            "HSE-L117-§72",
        ),
        concepts={
            "forklift": ConceptSpec(prompt="forklift", static=False),
            "person": ConceptSpec(prompt="person", static=False),
        },
        zones=_zones("aisle", cameras, (80.0, 120.0, 1200.0, 690.0, 1240.0, 40.0)),
        calibration="ground-plane@CAM-02",
        predicate=All(
            op="all",
            of=(
                DistanceM(op="distance_m", a="person", b="forklift", lt=2.0, gt=None),
                Moving(op="moving", object="forklift", speed_mps_gt=0.2),
            ),
        ),
        severity=Severity.HIGH,
        actions=(
            ActionSpec(kind="speak", role="operator", lang="en", after_s=0.0),
            ActionSpec(kind="notify", role="safety_officer", lang="en", after_s=0.0),
        ),
        verify=_verify(0.7),
        accept=("person within 2 m of a moving forklift", "exclude seated operator inside cab"),
        backtestable="yes",
    )


def _ppe_rule(plan_id: str) -> RuleIR:
    cameras = ("CAM-03", "CAM-06", "CAM-12")
    return RuleIR(
        id="require-hardhat-in-visitor-corridor",
        version=2,
        source=_source(
            "Every person entering the East Visitor & PPE Corridor must wear a safety hardhat.",
            plan_id,
            "OSHA-1910.135(a)(1)",
        ),
        concepts={
            "person": ConceptSpec(prompt="person", static=False),
            "safety_helmet": ConceptSpec(prompt="safety helmet", static=False),
        },
        zones=_zones("corridor", cameras, (100.0, 100.0, 1180.0, 680.0, 1200.0, 80.0)),
        calibration="ground-plane@CAM-03",
        predicate=All(
            op="all",
            of=(
                Inside(op="inside", object="person", zone="corridor_cam_03"),
                Without(op="without", subject="person", item="safety_helmet", region="top_25pct"),
            ),
        ),
        severity=Severity.MEDIUM,
        actions=(ActionSpec(kind="notify", role="supervisor", lang="en", after_s=2.0),),
        verify=_verify(0.75),
        accept=("visitor or worker in corridor without safety helmet for >2s",),
        backtestable="yes",
    )


def _yard_rule(plan_id: str) -> RuleIR:
    return RuleIR(
        id="heavy-equipment-swing-radius-exclusion",
        version=2,
        source=_source(
            "Maintain a 3.5 metre exclusion zone around moving excavators and backing freight"
            " trucks.",
            plan_id,
            "OSHA-1926.1424(a)(2)",
        ),
        concepts={
            "person": ConceptSpec(prompt="person", static=False),
            "excavator": ConceptSpec(prompt="excavator or heavy truck", static=False),
        },
        zones=_zones("yard", ("CAM-08", "CAM-09"), (60.0, 140.0, 1220.0, 700.0, 1240.0, 40.0)),
        calibration="ground-plane@CAM-09",
        predicate=All(
            op="all",
            of=(
                DistanceM(op="distance_m", a="person", b="excavator", lt=3.5, gt=None),
                Moving(op="moving", object="excavator", speed_mps_gt=0.15),
            ),
        ),
        severity=Severity.HIGH,
        actions=(
            ActionSpec(kind="interlock_cut", role="site_lead", lang="en", after_s=0.0),
            ActionSpec(kind="notify", role="safety_officer", lang="en", after_s=0.0),
        ),
        verify=_verify(0.7),
        accept=("worker within 3.5 m of swinging excavator boom or backing freight truck",),
        backtestable="yes",
    )


def _kitchen_rule(plan_id: str) -> RuleIR:
    return RuleIR(
        id="commercial-kitchen-prep-sanitation-guard",
        version=1,
        source=_source(
            "Ensure commercial kitchen prep line staff wear chef head coverings and maintain"
            " clear prep stations.",
            plan_id,
            "FDA-FoodCode-2-402.11",
        ),
        concepts={
            "person": ConceptSpec(prompt="chef or kitchen worker", static=False),
            "head_covering": ConceptSpec(prompt="chef hat or hairnet", static=False),
        },
        zones=_zones("prep_line", ("CAM-05",), (90.0, 110.0, 1190.0, 680.0, 1210.0, 70.0)),
        calibration="ground-plane@CAM-05",
        predicate=All(
            op="all",
            of=(
                Inside(op="inside", object="person", zone="prep_line_cam_05"),
                Without(op="without", subject="person", item="head_covering", region="top_25pct"),
            ),
        ),
        severity=Severity.LOW,
        actions=(ActionSpec(kind="notify", role="kitchen_lead", lang="en", after_s=5.0),),
        verify=_verify(0.7, tier2=False),
        accept=("kitchen staff on prep line without hairnet or chef hat",),
        backtestable="yes",
    )


def _inventory() -> tuple[InventoryItem, ...]:
    rows = (
        ("CAM-01", "forklift", 2, 0.942),
        ("CAM-01", "person", 3, 0.961),
        ("CAM-02", "forklift", 1, 0.938),
        ("CAM-02", "person", 2, 0.954),
        ("CAM-03", "person", 4, 0.968),
        ("CAM-03", "safety helmet", 1, 0.912),
        ("CAM-04", "forklift operator", 1, 0.960),
        ("CAM-04", "wooden pallet", 3, 0.884),
        ("CAM-05", "chef or kitchen worker", 4, 0.951),
        ("CAM-08", "freight truck", 1, 0.947),
        ("CAM-09", "excavator", 1, 0.964),
    )
    return tuple(
        InventoryItem(camera_id=cam, concept=concept, count=count, mean_confidence=conf)
        for cam, concept, count, conf in rows
    )


def _probe(camera: str, concept: str, rate: float, lb: float, px: float) -> Probe:
    return Probe(
        camera_id=camera,
        concept=concept,
        n_frames=24,
        detect_rate=rate,
        detect_lb=lb,
        median_px_height=px,
    )


def _feasibility(rules: Sequence[RuleIR]) -> tuple[FeasibilityVerdict, ...]:
    forklift, ppe, yard, kitchen = rules
    observable = (
        (
            forklift,
            "Forklifts, pedestrians and 48x40in pallets are visible on six cameras.",
            (
                _probe("CAM-01", "forklift", 1.0, 0.892, 340.0),
                _probe("CAM-02", "forklift", 1.0, 0.910, 380.0),
                _probe("CAM-02", "person", 1.0, 0.924, 310.0),
                _probe("CAM-04", "forklift", 1.0, 0.862, 719.0),
                _probe("phone-27", "forklift", 1.0, 0.862, 719.0),
            ),
        ),
        (
            ppe,
            "Safety helmets are visible on CAM-03, CAM-06 and CAM-12 with a head crop-zoom.",
            (
                _probe("CAM-03", "person", 1.0, 0.941, 420.0),
                _probe("CAM-03", "safety helmet", 0.958, 0.895, 68.0),
            ),
        ),
        (
            yard,
            "Excavator boom and freight truck are visible on CAM-08 and CAM-09.",
            (
                _probe("CAM-08", "excavator", 1.0, 0.915, 460.0),
                _probe("CAM-09", "excavator", 1.0, 0.932, 510.0),
            ),
        ),
        (
            kitchen,
            "Prep Line A and the staff's head coverings are visible on CAM-05.",
            (
                _probe("CAM-05", "person", 1.0, 0.948, 390.0),
                _probe("CAM-05", "head_covering", 0.958, 0.884, 64.0),
            ),
        ),
    )
    verdicts = [
        FeasibilityVerdict(
            concern=rule.source.text,
            verdict="observable",
            says=f"{SAMPLE} {says}",
            evidence=evidence,
        )
        for rule, says, evidence in observable
    ]
    verdicts.append(
        FeasibilityVerdict(
            concern="Monitor North Stairwell Blind Spot (CAM-07) for slip hazards.",
            verdict="not_observable",
            reason=f"{SAMPLE} CAM-07 is occluded or offline (0/24 probe frames).",
            alternative="Pair a phone at the Stairwell B landing, or re-angle CAM-06.",
            says=f"{SAMPLE} CAM-07 cannot see, so a rule there would not be activated.",
            evidence=(),
        )
    )
    return tuple(verdicts)


def _back_translation(rules: Sequence[RuleIR]) -> str:
    lines = [f"{SAMPLE} Seeded by scripts/seed_live_backend.py, not a planner run.", "[en]"]
    lines += [f"- {rule.id}: {rule.source.text}" for rule in rules]
    return "\n".join(lines)


def _master_plan(rules: tuple[RuleIR, ...]) -> SitePlan:
    """The four-rule sample plan (`MASTER_PLAN_ID`), every hand-written field labelled."""
    standards = ("OSHA 1910.178(n)(1)", "OSHA 1910.135(a)(1)", "OSHA 1926.1424(a)(2)")
    details = (*standards, "FDA Food Code §2-402.11")
    checks = [
        check
        for rule, detail in zip(rules, details, strict=True)
        for check in _rule_checks(rule.id, rule.source.text or rule.id, f"{detail} matched")
    ]
    return SitePlan(
        plan_id=MASTER_PLAN_ID,
        version=3,
        site_name=SITE,
        setting_inferred="warehouse",
        privacy_mode=PrivacyMode.STANDARD,
        retention_days=30,
        languages=("en", "es"),
        standards=tuple(s for rule in rules for s in rule.source.standard),
        roles={
            "safety_officer": "telegram:@safety_ops",
            "supervisor": "telegram:@floor_sup",
            "site_lead": "webhook:https://ops.example.invalid/hooks/site-lead",
            "kitchen_lead": "telegram:@kitchen_lead",
        },
        request=tuple(RequestItem(kind="text", text=r.source.text, lang="en") for r in rules),
        inventory=_inventory(),
        feasibility=_feasibility(rules),
        rules=rules,
        acceptance=AcceptanceResult(checks=tuple(checks), rounds=1),
        audit=(
            AuditFinding(
                by="spark-open",
                text=f"{SAMPLE} No Spark Open audit ran on this sample plan.",
                reasoning_ref=SEED_MARKER,
            ),
        ),
        back_translation=_back_translation(rules),
        cost_usd_per_cam_hour=0.14,
    )


def _save_plan(store: PlanStore, plan: SitePlan) -> None:
    store.plan_path(plan.plan_id).write_text(
        yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    store.save_input(
        PipelineInput(
            plan_id=plan.plan_id,
            site_name=plan.site_name,
            request=plan.request,
            roles=plan.roles,
            privacy_mode=plan.privacy_mode,
            retention_days=plan.retention_days,
            calibrations={cam: _sample_calibration(cam) for cam in ALL_CAMERAS},
            languages=plan.languages,
        )
    )


def build_plans() -> list[SitePlan]:
    """Build the golden plan and the two sample plans and write them to `plans/`.

    Returns:
        The golden `warehouse_driver` plan (a real recording, unchanged), the two-rule sample
        plan and the four-rule sample plan, in that order.
    """
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    store = PlanStore(PLANS_DIR)
    golden_file = ROOT / "scenarios" / "warehouse_driver" / "plan.json"
    golden_raw = json.loads(golden_file.read_text("utf-8"))
    golden_raw.setdefault("site_name", SITE)
    golden_raw.setdefault("version", 1)
    golden = SitePlan.model_validate(golden_raw)

    rules = tuple(
        build(MASTER_PLAN_ID) for build in (_forklift_rule, _ppe_rule, _yard_rule, _kitchen_rule)
    )
    master = _master_plan(rules)
    two_rules = master.model_copy(
        update={
            "plan_id": SAMPLE_PLAN_IDS[0],
            "version": 2,
            "rules": rules[:2],
            "acceptance": AcceptanceResult(
                checks=tuple(
                    check
                    for rule, detail in zip(
                        rules[:2], ("OSHA 1910.178(n)(1)", "OSHA 1910.135"), strict=True
                    )
                    for check in _rule_checks(
                        rule.id, rule.source.text or rule.id, f"{detail} matched"
                    )
                ),
                rounds=0,
            ),
        }
    )
    for plan in (golden, two_rules, master):
        _save_plan(store, plan)
    return [golden, two_rules, master]


# ---------------------------------------------------------------- sample evidence chain


def is_sample_chain(path: Path) -> bool:
    """Whether a chain file was written by this script (its verdicts carry `SEED_MARKER`).

    Args:
        path: A day's chain file.

    Returns:
        True for a sample chain; False for any other chain, or when the file does not exist.
    """
    return path.is_file() and SEED_MARKER in path.read_text(encoding="utf-8")


def _copy_golden_day(demo_dir: Path) -> None:
    """Copy the golden 1 Nov chain into the demo site, unless that day already has a chain."""
    target = demo_dir / f"{GOLDEN_DAY}.jsonl"
    if not GOLDEN_CHAIN.is_file():
        return
    if target.is_file() and target.read_bytes() != GOLDEN_CHAIN.read_bytes():
        _log.warning("seed_golden_day_kept", path=str(target), reason="a different chain exists")
        return
    shutil.copy2(GOLDEN_CHAIN, target)
    _write_sample_root(target, date.fromisoformat(GOLDEN_DAY))


def _write_sample_root(chain_file: Path, day: date) -> None:
    root_hex, n_records = chain_root(chain_file)
    SAMPLE_ROOTS_DIR.mkdir(parents=True, exist_ok=True)
    (SAMPLE_ROOTS_DIR / f"{day.isoformat()}.txt").write_text(
        f"{SITE} {root_hex} {n_records}\n", encoding="utf-8"
    )


def _incident_records(
    chain: EventChain, clock: _SteppingClock, spec: IncidentSpec, rule: RuleIR, plan_hash: str
) -> None:
    """Append one sample incident: candidate, verdict, action, and its ack or dispute."""
    t_end = clock.now()
    strips = DATA_DIR / "strips"
    strip_files = tuple(
        str((strips / f"{spec.camera_id}_strip_{i}.jpg").resolve())
        for i in range(3)
        if (strips / f"{spec.camera_id}_strip_{i}.jpg").is_file()
    )
    candidate = CandidateEvent(
        event_id=spec.event_id,
        rule_id=rule.id,
        rule_ir_hash=rule_ir_hash(rule),
        plan_version_hash=plan_hash,
        camera_id=spec.camera_id,
        ts_start=t_end - timedelta(seconds=3, milliseconds=200),
        ts_end=t_end,
        severity=rule.severity,
        measurement=Measurement(value=spec.value, unit=spec.unit),
        predicate_trace={
            "all": True,
            "all.0.distance_m": True,
            "all.0.distance_m.value": spec.value,
            "all.1.moving": True,
            "all.1.moving.value": spec.speed,
            "all.value": spec.value,
            "episode_s": 3.2,
            "subject": spec.subject,
        },
        track_window_hash=hashlib.sha256(spec.event_id.encode()).hexdigest(),
        strip_paths=strip_files,
    )
    context = DecisionContext(
        rule=rule,
        calibration=_sample_calibration(spec.camera_id),
        zones={},
        t0=t_end - timedelta(seconds=30),
        t1=t_end,
        hz=10.0,
        plan_version_hash=plan_hash,
        operator_exclusion=True,
    )
    chain.append("candidate", candidate_payload(candidate, context))
    verdict = VerifierVerdict(
        verdict=spec.verdict,
        score=spec.score,
        reason=f"{SAMPLE} {spec.reason}",
        tier="glimmer",
        model_version=SAMPLE_MODEL,
        latency_ms=0,
        tamper=False,
        scene_text="",
    )
    chain.append("verdict", {**verdict.model_dump(mode="json"), "event_id": spec.event_id})
    chain.append(
        "action",
        {
            "event_id": spec.event_id,
            "kind": spec.action_kind,
            "role": spec.action_role,
            "gate": "auto",
            "ok": True,
            "detail": f"{SAMPLE} {spec.action_detail}",
            "approved_by_role": None,
            "ts": clock.now().isoformat().replace("+00:00", "Z"),
        },
    )
    if spec.ack_role:
        chain.append(
            "ack", {"event_id": spec.event_id, "role": spec.ack_role, "action": "acknowledged"}
        )
    if spec.dispute is not None:
        _dispute_record(chain, spec, t_end.date())


def _dispute_record(chain: EventChain, spec: IncidentSpec, day: date) -> None:
    assert spec.dispute is not None  # noqa: S101 - the caller only passes disputed incidents
    choice, text, outcome = spec.dispute
    logged = read_site_log(chain, day, 30).events.get(spec.event_id)
    if logged is None:
        return
    dispute = Dispute(
        channel="tap",
        reason_text=f"{choice}: {text}",
        outcome=outcome,
        reviewer_reasoning=f"{SAMPLE} Seeded outcome; no Tier 2 review ran.",
    )
    chain.append("dispute", dispute_payload(f"disp-{spec.event_id[-8:]}", logged, dispute))


def seed_chains_and_activation(plans: list[SitePlan], now: datetime, *, force: bool) -> None:
    """Write today's sample chain for the demo site and activate the four-rule sample plan.

    Args:
        plans: From `build_plans`; the last is activated.
        now: The time today's records end near.
        force: Replace today's chain even when this script did not write it.

    Raises:
        SystemExit: When today's chain is a real one and `force` is not set.
        RuntimeError: When the written chain does not verify.
    """
    chain_dir = DATA_DIR / "evidence" / "chain"
    demo_dir = chain_dir / SITE
    demo_dir.mkdir(parents=True, exist_ok=True)
    _copy_golden_day(demo_dir)

    today = now.date()
    today_path = demo_dir / f"{today.isoformat()}.jsonl"
    if today_path.is_file() and not is_sample_chain(today_path) and not force:
        msg = (
            f"{today_path} is a real evidence chain (not written by this script); refusing to"
            " replace it. Run on a development data folder, or pass --force."
        )
        raise SystemExit(msg)
    today_path.unlink(missing_ok=True)

    clock = _SteppingClock(now - timedelta(minutes=28))
    chain = EventChain(chain_dir, SITE, clock=clock)
    store = PlanStore(PLANS_DIR)
    activator = PlanActivator(
        runtime=_RecordingRuntime(),  # type: ignore[arg-type]
        chain=chain,
        state_path=DATA_DIR / "runtime" / "active_plan.json",
        calibrations=lambda plan: store.calibrations(plan.plan_id),
    )
    master = plans[-1]
    plan_hash = activator.activate(master).active_plan.plan_version_hash
    chain.append(
        "coverage",
        {
            "cameras_total": len(ALL_CAMERAS),
            "cameras_watched": len(ALL_CAMERAS) - 1,
            "watched_fraction": 0.964,
            "blind_cameras": ["CAM-07"],
        },
    )
    rules = {rule.id: rule for rule in master.rules}
    for spec in INCIDENTS:
        _incident_records(chain, clock, spec, rules[spec.rule_id], plan_hash)

    report = verify_chain(today_path)
    if not report.ok:
        msg = f"chain verification failed at seq {report.first_bad_seq}: {report.reason}"
        raise RuntimeError(msg)
    _write_sample_root(today_path, today)
    _log.info("seed_chain_written", path=str(today_path), sample=True)


def register_with_api(plans: Sequence[SitePlan], api: str) -> None:
    """Hand the plans to a running local API and approve the last one, when it answers.

    Args:
        plans: From `build_plans`.
        api: The local API's base URL.
    """
    try:
        for plan in plans:
            body = plan.model_dump(mode="json")
            httpx.post(f"{api}/plans", json=body, timeout=HTTP_TIMEOUT_S)
        httpx.post(f"{api}/plans/{plans[-1].plan_id}/approve", timeout=HTTP_TIMEOUT_S)
    except httpx.HTTPError as exc:
        _log.warning("seed_api_unreachable", api=api, error=str(exc))


def main(argv: Sequence[str] | None = None) -> None:
    """Seed the local backend with the sample data (see the module docstring)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--force", action="store_true", help="replace today's chain even if it is a real one"
    )
    parser.add_argument("--api", default="http://localhost:8000", help="local API base URL")
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    seed_chunks(prepare_videos_and_strips(), now)
    plans = build_plans()
    seed_chains_and_activation(plans, now, force=args.force)
    register_with_api(plans, args.api)
    _log.info("seed_done", plans=[p.plan_id for p in plans], sample=True)


if __name__ == "__main__":
    main()
