"""Seed the live Rulecam backend with video streams, chunks, SitePlans and SHA-256 EventChains."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
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
from rulecam.planner.pipeline import PipelineInput, PlanStore
from rulecam.rules.backtest import DecisionContext, candidate_payload
from rulecam.rules.hashing import rule_ir_hash
from rulecam.runtime.activate import PlanActivator
from rulecam.worker.log import dispute_payload, read_site_log

ROOT = Path(__file__).resolve().parent.parent
RAW_CLIPS = ROOT / "data" / "raw_clips"
VIDEOS_DIR = ROOT / "ui" / "public" / "videos"
PUBLIC_STRIPS = ROOT / "ui" / "public" / "strips"
DATA_DIR = ROOT / "data"
PLANS_DIR = ROOT / "plans"
ROOTS_DIR = ROOT / "evidence" / "roots"


CAMERA_SPECS = [
    ("CAM-01", "North Loading Dock · Bay 1-3", "warehouse_walk_23550.mp4", "cam01_dock.mp4"),
    ("CAM-02", "Main Aisle B2 Intersection", "engineer_walk_23406.mp4", "cam02_intersection.mp4"),
    ("CAM-03", "East Visitor & PPE Corridor", "warehouse_group_23237.mp4", "cam03_ppe.mp4"),
    ("CAM-04", "Staging Bay C4 · Forklift Cab", "forklift_driver_45848.mp4", "cam04_forklift.mp4"),
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
]


HOMOGRAPHY = (
    (0.005486241729051363, 0.0004220185945424167, -4.175029955808087),
    (-3.102079079558648e-19, 0.05522316095296006, 5.908614624992072e-16),
    (-0.00037807183364839365, -0.00046691273871012545, 0.9999999999999999),
)


def prepare_videos_and_strips() -> dict[str, Path]:
    """Copy raw HD clips into `ui/public/videos/` and extract keyframe JPEG strips."""
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_STRIPS.mkdir(parents=True, exist_ok=True)
    strips_dir = DATA_DIR / "strips"
    strips_dir.mkdir(parents=True, exist_ok=True)

    cam_paths: dict[str, Path] = {}
    for cam_id, _label, src_name, dst_name in CAMERA_SPECS:
        src = RAW_CLIPS / src_name
        dst = VIDEOS_DIR / dst_name
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)
        cam_paths[cam_id] = dst if dst.is_file() else src

    # Extract 3 keyframe JPEGs per camera for incident strips & planner frame taps
    frames_dir = DATA_DIR / "planner_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for cam_id, path in cam_paths.items():
        if not path.is_file():
            continue
        if all((strips_dir / f"{cam_id}_strip_{idx}.jpg").is_file() for idx in range(3)):
            continue
        cap = cv2.VideoCapture(str(path))
        total = max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 1)
        for idx, frac in enumerate((0.2, 0.5, 0.8)):
            target = min(int(total * frac), total - 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ok, frame = cap.read()
            if ok and frame is not None:
                strip_file = strips_dir / f"{cam_id}_strip_{idx}.jpg"
                pub_file = PUBLIC_STRIPS / f"{cam_id}_strip_{idx}.jpg"
                cv2.imwrite(str(strip_file), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
                shutil.copy2(strip_file, pub_file)
                if idx == 1:
                    cv2.imwrite(str(frames_dir / f"{cam_id}.jpg"), frame)
        cap.release()
    return cam_paths


def seed_chunks(cam_paths: dict[str, Path], now: datetime) -> None:
    """Write `data/chunks/index.jsonl` so `GET /cameras` serves all active cameras."""
    chunks_dir = DATA_DIR / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    index_file = chunks_dir / "index.jsonl"
    lines: list[str] = []
    for i, (cam_id, _label, _src, _dst) in enumerate(CAMERA_SPECS):
        path = cam_paths[cam_id]
        if not path.is_file():
            continue
        cap = cv2.VideoCapture(str(path))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        n_frames = max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 150), 1)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
        cap.release()
        chunk = Chunk(
            chunk_id=f"chk-{cam_id.lower()}-{i:03d}",
            camera_id=cam_id,
            path=str(path.resolve()),
            start_ts=now - timedelta(seconds=12 - (i % 9)),
            fps=fps,
            n_frames=n_frames,
            width=width,
            height=height,
            provenance=Provenance.RECORDED,
        )
        lines.append(chunk.model_dump_json())
    index_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rule_checks(
    rule_id: str, concern_text: str, standard_detail: str = "OSHA 1910.178(n)(1) matched"
) -> list[AcceptanceCheck]:
    return [
        AcceptanceCheck(
            check="concern_covered",
            item=concern_text,
            passed=True,
            detail=f"rule '{rule_id}'",
        ),
        AcceptanceCheck(
            check="zone_in_view",
            item=rule_id,
            passed=True,
            detail="all monitored zones visible in camera frame",
        ),
        AcceptanceCheck(
            check="detect_lb>=0.8",
            item=rule_id,
            passed=True,
            detail="lowest detect_lb 0.89 (24/24 probe frames)",
        ),
        AcceptanceCheck(
            check="calibration_err<=tol",
            item=rule_id,
            passed=True,
            detail="calibrated within 0.094 m (tol 0.10 m)",
        ),
        AcceptanceCheck(
            check="privacy_consistent",
            item=rule_id,
            passed=True,
            detail="consistent with standard mode (face blur + non-biometric tracks)",
        ),
        AcceptanceCheck(
            check="citation_text_match",
            item=rule_id,
            passed=True,
            detail=standard_detail,
        ),
        AcceptanceCheck(
            check="no_private_spaces",
            item=rule_id,
            passed=True,
            detail="no private-space zones",
        ),
        AcceptanceCheck(
            check="ci_green",
            item=rule_id,
            passed=True,
            detail="boundary 16/16, mutations 6/6",
        ),
        AcceptanceCheck(
            check="nonvacuous",
            item=rule_id,
            passed=True,
            detail="fires on its positive boundary trace and not on its negative one",
        ),
    ]


def build_plans() -> list[SitePlan]:
    """Build 3 complete, schema-valid SitePlans and persist them to `plans/`."""
    store = PlanStore(PLANS_DIR)
    PLANS_DIR.mkdir(parents=True, exist_ok=True)

    # Plan 1: Golden scenario warehouse_driver plan (plan-4f9ee0094e29)
    golden_raw = json.loads(
        (ROOT / "scenarios" / "warehouse_driver" / "plan.json").read_text("utf-8")
    )
    golden_raw.setdefault("site_name", "demo")
    golden_raw.setdefault("version", 1)
    plan_v1 = SitePlan.model_validate(golden_raw)

    # Plan 3: Full 12-camera Master Production Plan (plan-c914e72a03f5, v3)
    plan_id_v3 = "plan-c914e72a03f5"
    all_cams = [
        "CAM-01",
        "CAM-02",
        "CAM-03",
        "CAM-04",
        "CAM-05",
        "CAM-06",
        "CAM-08",
        "CAM-09",
        "CAM-10",
        "CAM-11",
        "CAM-12",
        "phone-27",
    ]

    # Create zones across cameras so `/worker/{plan_id}/{camera}` serves every active camera
    forklift_zones = {
        f"aisle_{cam.lower().replace('-', '_')}": ZoneSpec(
            camera_id=cam,
            kind="polygon",
            polygon_px=((80.0, 120.0), (1200.0, 120.0), (1240.0, 690.0), (40.0, 690.0)),
        )
        for cam in ("CAM-01", "CAM-02", "CAM-04", "CAM-10", "CAM-11", "phone-27")
    }
    ppe_zones = {
        f"corridor_{cam.lower().replace('-', '_')}": ZoneSpec(
            camera_id=cam,
            kind="polygon",
            polygon_px=((100.0, 100.0), (1180.0, 100.0), (1200.0, 680.0), (80.0, 680.0)),
        )
        for cam in ("CAM-03", "CAM-06", "CAM-12")
    }
    yard_zones = {
        f"yard_{cam.lower().replace('-', '_')}": ZoneSpec(
            camera_id=cam,
            kind="polygon",
            polygon_px=((60.0, 140.0), (1220.0, 140.0), (1240.0, 700.0), (40.0, 700.0)),
        )
        for cam in ("CAM-08", "CAM-09")
    }
    kitchen_zones = {
        "prep_line_cam_05": ZoneSpec(
            camera_id="CAM-05",
            kind="polygon",
            polygon_px=((90.0, 110.0), (1190.0, 110.0), (1210.0, 680.0), (70.0, 680.0)),
        )
    }

    rule_forklift = RuleIR(
        id="keep-people-2m-from-moving-forklift",
        version=3,
        source=RuleSource(
            kind="text",
            text="Keep people at least 2 metres away from a moving forklift.",
            lang="en",
            plan_id=plan_id_v3,
            standard=("OSHA-1910.178(n)(1)", "HSE-L117-§72"),
        ),
        concepts={
            "forklift": ConceptSpec(prompt="forklift", static=False),
            "person": ConceptSpec(prompt="person", static=False),
        },
        zones=forklift_zones,
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
        verify=VerifySpec(tier1="glimmer", tier2="spark-open-on-disagree", min_confidence=0.7),
        accept=("person within 2 m of a moving forklift", "exclude seated operator inside cab"),
        backtestable="yes",
    )

    rule_ppe = RuleIR(
        id="require-hardhat-in-visitor-corridor",
        version=2,
        source=RuleSource(
            kind="text",
            text=(
                "Every person entering the East Visitor & PPE Corridor must wear a safety hardhat."
            ),
            lang="en",
            plan_id=plan_id_v3,
            standard=("OSHA-1910.135(a)(1)",),
        ),
        concepts={
            "person": ConceptSpec(prompt="person", static=False),
            "safety_helmet": ConceptSpec(prompt="safety helmet", static=False),
        },
        zones=ppe_zones,
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
        verify=VerifySpec(tier1="glimmer", tier2="spark-open-on-disagree", min_confidence=0.75),
        accept=("visitor or worker in corridor without safety helmet for >2s",),
        backtestable="yes",
    )

    rule_yard = RuleIR(
        id="heavy-equipment-swing-radius-exclusion",
        version=2,
        source=RuleSource(
            kind="text",
            text=(
                "Maintain a 3.5 metre exclusion zone around moving excavators and backing "
                "freight trucks."
            ),
            lang="en",
            plan_id=plan_id_v3,
            standard=("OSHA-1926.1424(a)(2)",),
        ),
        concepts={
            "person": ConceptSpec(prompt="person", static=False),
            "excavator": ConceptSpec(prompt="excavator or heavy truck", static=False),
        },
        zones=yard_zones,
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
        verify=VerifySpec(tier1="glimmer", tier2="spark-open-on-disagree", min_confidence=0.7),
        accept=("worker within 3.5 m of swinging excavator boom or backing freight truck",),
        backtestable="yes",
    )

    rule_kitchen = RuleIR(
        id="commercial-kitchen-prep-sanitation-guard",
        version=1,
        source=RuleSource(
            kind="text",
            text=(
                "Ensure commercial kitchen prep line staff wear chef head coverings and maintain "
                "clear prep stations."
            ),
            lang="en",
            plan_id=plan_id_v3,
            standard=("FDA-FoodCode-2-402.11",),
        ),
        concepts={
            "person": ConceptSpec(prompt="chef or kitchen worker", static=False),
            "head_covering": ConceptSpec(prompt="chef hat or hairnet", static=False),
        },
        zones=kitchen_zones,
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
        verify=VerifySpec(tier1="glimmer", tier2="none", min_confidence=0.7),
        accept=("kitchen staff on prep line without hairnet or chef hat",),
        backtestable="yes",
    )

    checks_v3 = (
        _rule_checks(
            rule_forklift.id,
            rule_forklift.source.text,
            "OSHA 1910.178(n)(1) & HSE L117 §72 verified",
        )
        + _rule_checks(rule_ppe.id, rule_ppe.source.text, "OSHA 1910.135(a)(1) verified")
        + _rule_checks(rule_yard.id, rule_yard.source.text, "OSHA 1926.1424(a)(2) verified")
        + _rule_checks(
            rule_kitchen.id, rule_kitchen.source.text, "FDA Food Code §2-402.11 verified"
        )
    )

    back_translation_v3 = (
        "[en]\n"
        f"- {rule_forklift.id}: Monitors forklifts and pedestrians across loading docks and "
        "aisles (CAM-01, CAM-02, CAM-04, CAM-10, CAM-11, phone-27). Triggers with HIGH severity "
        "when a person is within 2.0 m of a forklift moving faster than 0.2 m/s (seated "
        "operators inside the cab are automatically excluded). Action: play audible warning and "
        "notify safety_officer immediately.\n"
        f"- {rule_ppe.id}: Monitors the East Visitor & PPE Corridor (CAM-03, CAM-06, CAM-12). "
        "Triggers with MEDIUM severity when a person is inside the corridor without a safety "
        "helmet for over 2.0 s. Action: notify supervisor.\n"
        f"- {rule_yard.id}: Monitors the South Freight Gate and Yard Heavy Equipment Bay (CAM-08, "
        "CAM-09). Triggers with HIGH severity when a person is within 3.5 m of a moving "
        "excavator boom or backing truck (>0.15 m/s). Action: trip zone interlock and notify "
        "safety_officer.\n"
        f"- {rule_kitchen.id}: Monitors Commercial Kitchen Prep Line A (CAM-05). Triggers with "
        "LOW severity when staff work at the prep line without a chef hat or hairnet for over "
        "5.0 s. Action: notify kitchen_lead.\n"
        "[es]\n"
        f"- {rule_forklift.id}: Monitorea montacargas y peatones en muelles y pasillos (CAM-01, "
        "CAM-02, CAM-04, CAM-10, CAM-11, phone-27). Se activa con severidad ALTA cuando una "
        "persona está a menos de 2.0 m de un montacargas en movimiento (>0.2 m/s; el operador "
        "sentado queda excluido). Acción: alerta de voz inmediata y notificación al oficial de "
        "seguridad.\n"
        f"- {rule_ppe.id}: Monitorea el Corredor Este de Visitantes y EPP (CAM-03, CAM-06, "
        "CAM-12). Se activa con severidad MEDIA cuando una persona entra sin casco de seguridad "
        "por más de 2.0 s. Acción: notificar al supervisor.\n"
        f"- {rule_yard.id}: Monitorea la Puerta Sur de Carga y Bahía de Maquinaria Pesada "
        "(CAM-08, CAM-09). Se activa con severidad ALTA cuando un peatón está a menos de 3.5 m "
        "de una excavadora o camión en movimiento. Acción: activar bloqueo de seguridad y "
        "notificar al oficial.\n"
        f"- {rule_kitchen.id}: Monitorea la Línea de Preparación de Cocina Comercial A (CAM-05). "
        "Se activa con severidad BAJA cuando el personal trabaja sin gorro o malla para el "
        "cabello por más de 5.0 s."
    )

    plan_v3 = SitePlan(
        plan_id=plan_id_v3,
        version=3,
        site_name="demo",
        setting_inferred="warehouse",
        privacy_mode=PrivacyMode.STANDARD,
        retention_days=30,
        languages=("en", "es"),
        standards=(
            "OSHA-1910.178(n)(1)",
            "HSE-L117-§72",
            "OSHA-1910.135(a)(1)",
            "OSHA-1926.1424(a)(2)",
            "FDA-FoodCode-2-402.11",
        ),
        roles={
            "safety_officer": "telegram:@safety_ops",
            "supervisor": "telegram:@floor_sup",
            "site_lead": "webhook:https://ops.axi0n.internal/hooks/site-lead",
            "kitchen_lead": "telegram:@kitchen_lead",
        },
        request=(
            RequestItem(kind="text", text=rule_forklift.source.text, lang="en"),
            RequestItem(kind="text", text=rule_ppe.source.text, lang="en"),
            RequestItem(kind="text", text=rule_yard.source.text, lang="en"),
            RequestItem(kind="text", text=rule_kitchen.source.text, lang="en"),
        ),
        inventory=(
            InventoryItem(camera_id="CAM-01", concept="forklift", count=2, mean_confidence=0.942),
            InventoryItem(camera_id="CAM-01", concept="person", count=3, mean_confidence=0.961),
            InventoryItem(camera_id="CAM-02", concept="forklift", count=1, mean_confidence=0.938),
            InventoryItem(camera_id="CAM-02", concept="person", count=2, mean_confidence=0.954),
            InventoryItem(camera_id="CAM-03", concept="person", count=4, mean_confidence=0.968),
            InventoryItem(
                camera_id="CAM-03", concept="safety helmet", count=1, mean_confidence=0.912
            ),
            InventoryItem(
                camera_id="CAM-04", concept="forklift operator", count=1, mean_confidence=0.960
            ),
            InventoryItem(
                camera_id="CAM-04", concept="wooden pallet", count=3, mean_confidence=0.884
            ),
            InventoryItem(
                camera_id="CAM-05", concept="chef or kitchen worker", count=4, mean_confidence=0.951
            ),
            InventoryItem(
                camera_id="CAM-08", concept="freight truck", count=1, mean_confidence=0.947
            ),
            InventoryItem(camera_id="CAM-09", concept="excavator", count=1, mean_confidence=0.964),
        ),
        feasibility=(
            FeasibilityVerdict(
                concern=rule_forklift.source.text,
                verdict="observable",
                says=(
                    "Forklifts, pedestrians, and 48x40in reference pallets are clearly visible "
                    "across CAM-01, CAM-02, CAM-04, CAM-10, CAM-11, and phone-27 with "
                    "lower-bound detection >= 0.89."
                ),
                evidence=(
                    Probe(
                        camera_id="CAM-01",
                        concept="forklift",
                        n_frames=24,
                        detect_rate=1.0,
                        detect_lb=0.892,
                        median_px_height=340.0,
                    ),
                    Probe(
                        camera_id="CAM-02",
                        concept="forklift",
                        n_frames=24,
                        detect_rate=1.0,
                        detect_lb=0.910,
                        median_px_height=380.0,
                    ),
                    Probe(
                        camera_id="CAM-02",
                        concept="person",
                        n_frames=24,
                        detect_rate=1.0,
                        detect_lb=0.924,
                        median_px_height=310.0,
                    ),
                    Probe(
                        camera_id="CAM-04",
                        concept="forklift",
                        n_frames=24,
                        detect_rate=1.0,
                        detect_lb=0.862,
                        median_px_height=719.0,
                    ),
                    Probe(
                        camera_id="phone-27",
                        concept="forklift",
                        n_frames=24,
                        detect_rate=1.0,
                        detect_lb=0.862,
                        median_px_height=719.0,
                    ),
                ),
            ),
            FeasibilityVerdict(
                concern=rule_ppe.source.text,
                verdict="observable",
                says=(
                    "SAM 3.1 + Tier-0 head crop-zoom detects safety helmets on CAM-03, CAM-06, "
                    "and CAM-12 at 0.91 lower bound."
                ),
                evidence=(
                    Probe(
                        camera_id="CAM-03",
                        concept="person",
                        n_frames=24,
                        detect_rate=1.0,
                        detect_lb=0.941,
                        median_px_height=420.0,
                    ),
                    Probe(
                        camera_id="CAM-03",
                        concept="safety helmet",
                        n_frames=24,
                        detect_rate=0.958,
                        detect_lb=0.895,
                        median_px_height=68.0,
                    ),
                ),
            ),
            FeasibilityVerdict(
                concern=rule_yard.source.text,
                verdict="observable",
                says=(
                    "Excavator boom and freight truck ground homography calibrated on CAM-08 and "
                    "CAM-09 within 0.088 m."
                ),
                evidence=(
                    Probe(
                        camera_id="CAM-08",
                        concept="excavator",
                        n_frames=24,
                        detect_rate=1.0,
                        detect_lb=0.915,
                        median_px_height=460.0,
                    ),
                    Probe(
                        camera_id="CAM-09",
                        concept="excavator",
                        n_frames=24,
                        detect_rate=1.0,
                        detect_lb=0.932,
                        median_px_height=510.0,
                    ),
                ),
            ),
            FeasibilityVerdict(
                concern=rule_kitchen.source.text,
                verdict="observable",
                says="Prep Line A counter and staff head coverings clearly observable on CAM-05.",
                evidence=(
                    Probe(
                        camera_id="CAM-05",
                        concept="person",
                        n_frames=24,
                        detect_rate=1.0,
                        detect_lb=0.948,
                        median_px_height=390.0,
                    ),
                    Probe(
                        camera_id="CAM-05",
                        concept="head_covering",
                        n_frames=24,
                        detect_rate=0.958,
                        detect_lb=0.884,
                        median_px_height=64.0,
                    ),
                ),
            ),
            FeasibilityVerdict(
                concern="Monitor North Stairwell Blind Spot (CAM-07) for slip hazards.",
                verdict="not_observable",
                reason="CAM-07 optical feed is occluded / offline (0/24 probe frames).",
                alternative=(
                    "Mount a paired mobile camera (Pair-a-Phone) at Stairwell B landing or "
                    "re-angle CAM-06."
                ),
                says=(
                    "I cannot verify slip hazards on CAM-07 because the camera feed is currently "
                    "blind; I refuse to activate an unobservable rule."
                ),
                evidence=(),
            ),
        ),
        rules=(rule_forklift, rule_ppe, rule_yard, rule_kitchen),
        acceptance=AcceptanceResult(checks=tuple(checks_v3), rounds=1),
        audit=(
            AuditFinding(
                by="spark-open",
                text=(
                    "Audited 4 rules against OSHA 1910.178, 1910.135, 1926.1424, and FDA Food "
                    "Code. Operator exclusion warm-up guard (1.5s) verified active on forklift "
                    "proximity rule; zero biometric or productivity tracking predicates present."
                ),
                reasoning_ref="spark-open-audit-c914e72a03f5",
            ),
        ),
        back_translation=back_translation_v3,
        cost_usd_per_cam_hour=0.14,
    )

    # Plan 2: Intermediate v2 plan (plan-8b21c409a7e1)
    plan_v2 = plan_v3.model_copy(
        update={
            "plan_id": "plan-8b21c409a7e1",
            "version": 2,
            "rules": (rule_forklift, rule_ppe),
            "acceptance": AcceptanceResult(
                checks=tuple(
                    _rule_checks(rule_forklift.id, rule_forklift.source.text)
                    + _rule_checks(rule_ppe.id, rule_ppe.source.text)
                ),
                rounds=0,
            ),
        }
    )

    calibrations = {
        cam: Calibration(
            camera_id=cam,
            homography=HOMOGRAPHY,
            method="reference_object",
            reference="pallet_48x40in",
            err_m=0.094,
        )
        for cam in all_cams
    }

    for plan in (plan_v1, plan_v2, plan_v3):
        yaml_path = store.plan_path(plan.plan_id)
        yaml_path.write_text(
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
                calibrations=calibrations,
                languages=plan.languages,
            )
        )

    return [plan_v1, plan_v2, plan_v3]


def seed_chains_and_activation(  # noqa: C901, PLR0915 - one linear seeding sequence
    plans: list[SitePlan], now: datetime
) -> None:
    """Seed both 2026-11-01 (golden chain) and today's live chain with verified SHA-256 records."""
    chain_dir = DATA_DIR / "evidence" / "chain"
    demo_dir = chain_dir / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    ROOTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Copy golden 2026-11-01 chain and commit its Merkle root
    golden_chain = ROOT / "ui" / "public" / "golden" / "warehouse_driver" / "chain.jsonl"
    if golden_chain.is_file():
        day_2026_11_01 = demo_dir / "2026-11-01.jsonl"
        shutil.copy2(golden_chain, day_2026_11_01)
        root_hex, n_rec = chain_root(day_2026_11_01)
        (ROOTS_DIR / "2026-11-01.txt").write_text(f"demo {root_hex} {n_rec}\n", encoding="utf-8")

    # 2. Build today's chain cleanly from scratch using EventChain + PlanActivator
    today = now.date()
    today_path = demo_dir / f"{today.isoformat()}.jsonl"
    today_path.unlink(missing_ok=True)

    master_plan = plans[-1]
    rule_map = {r.id: r for r in master_plan.rules}

    class SteppingClock:
        def __init__(self, start: datetime) -> None:
            self._cur = start

        def now(self) -> datetime:
            self._cur += timedelta(seconds=2, microseconds=142000)
            return self._cur

    clock = SteppingClock(now - timedelta(minutes=28))
    chain = EventChain(chain_dir, "demo", clock=clock)

    class _DummyRuntime:
        def __init__(self) -> None:
            self.plan = None

        def set_plan(self, active: object) -> None:
            self.plan = active

    dummy_rt = _DummyRuntime()
    store = PlanStore(PLANS_DIR)
    activator = PlanActivator(
        runtime=dummy_rt,  # type: ignore[arg-type]
        chain=chain,
        state_path=DATA_DIR / "runtime" / "active_plan.json",
        calibrations=lambda plan: store.calibrations(plan.plan_id),
    )
    activation = activator.activate(master_plan)
    plan_vhash = activation.active_plan.plan_version_hash

    # Coverage record
    chain.append(
        "coverage",
        {
            "cameras_total": 12,
            "cameras_watched": 11,
            "watched_fraction": 0.964,
            "blind_cameras": ["CAM-07"],
        },
    )

    strips_dir = DATA_DIR / "strips"

    incidents_spec = [
        {
            "event_id": "ev-91a4c802d3e177b54012a9c1",
            "rule_id": "keep-people-2m-from-moving-forklift",
            "camera_id": "CAM-02",
            "value": 1.18,
            "unit": "m",
            "speed": 1.84,
            "subject": "CAM-02:person:84a1c02b",
            "verdict": "violation",
            "score": 0.96,
            "reason": (
                "A pedestrian in a yellow safety vest walks directly into Aisle B2 intersection "
                "1.18 m ahead of a laterally crossing counterbalance forklift moving at 1.84 "
                "m/s. Clear 2.0 m separation breach confirmed."
            ),
            "action_kind": "speak",
            "action_role": "operator",
            "action_detail": (
                "played audible proximity horn + notified safety_officer (1.18 m < 2.00 m)"
            ),
            "ack_role": "safety_officer",
            "dispute": None,
        },
        {
            "event_id": "ev-2e3f2c20bf90ae8efa75c8c7",
            "rule_id": "keep-people-2m-from-moving-forklift",
            "camera_id": "CAM-04",
            "value": 0.08,
            "unit": "m",
            "speed": 0.62,
            "subject": "CAM-04:person:fe37c5cd",
            "verdict": "no_violation",
            "score": 0.04,
            "reason": (
                "The person at 0.08 m is seated inside the forklift cab operating the steering "
                "wheel and mast controls. Operator exclusion guard + Glimmer VLM confirm no "
                "pedestrian bystander is near the moving forklift."
            ),
            "action_kind": "notify",
            "action_role": "safety_officer",
            "action_detail": "suppressed by Glimmer Tier-1 verifier (seated operator inside cab)",
            "ack_role": None,
            "dispute": (
                "rule_was_followed",
                "Seated operator inside forklift cab, not a pedestrian.",
                "upheld",
            ),
        },
        {
            "event_id": "ev-3d82f109c4b681e29043b7a2",
            "rule_id": "require-hardhat-in-visitor-corridor",
            "camera_id": "CAM-03",
            "value": 3.0,
            "unit": "persons",
            "speed": 0.85,
            "subject": "CAM-03:person:3c90e11a",
            "verdict": "violation",
            "score": 0.94,
            "reason": (
                "Tier-0 SAM head crop-zoom and Glimmer confirm 1 guide wearing a red safety "
                "helmet accompanied by 3 visitors in business attire walking through the East "
                "Corridor without hardhats for 6.4 seconds."
            ),
            "action_kind": "notify",
            "action_role": "supervisor",
            "action_detail": (
                "notified floor supervisor: 3 unhelmeted visitors in East Corridor (CAM-03)"
            ),
            "ack_role": "supervisor",
            "dispute": None,
        },
        {
            "event_id": "ev-8c45d210e7f392a16094c8b3",
            "rule_id": "heavy-equipment-swing-radius-exclusion",
            "camera_id": "CAM-09",
            "value": 2.14,
            "unit": "m",
            "speed": 0.95,
            "subject": "CAM-09:person:71b4d90e",
            "verdict": "violation",
            "score": 0.95,
            "reason": (
                "Ground worker enters within 2.14 m of the rotating hydraulic excavator boom and "
                "dump truck loading zone during active slewing (>0.15 m/s), violating the 3.50 m "
                "exclusion radius."
            ),
            "action_kind": "interlock",
            "action_role": "site_lead",
            "action_detail": "tripped Yard Bay 9 swing beacon interlock + notified safety_officer",
            "ack_role": "site_lead",
            "dispute": None,
        },
        {
            "event_id": "ev-5a92e641b8c043d71029f5e6",
            "rule_id": "heavy-equipment-swing-radius-exclusion",
            "camera_id": "CAM-08",
            "value": 1.62,
            "unit": "m",
            "speed": 1.12,
            "subject": "CAM-08:person:55d2a18f",
            "verdict": "violation",
            "score": 0.92,
            "reason": (
                "Pedestrian walks across the South Freight Gate loading apron 1.62 m behind a "
                "backing heavy freight truck moving at 1.12 m/s."
            ),
            "action_kind": "notify",
            "action_role": "safety_officer",
            "action_detail": "notified safety_officer + dock audio warning on CAM-08",
            "ack_role": "safety_officer",
            "dispute": None,
        },
        {
            "event_id": "ev-6f01b394a2d819c45081e3f4",
            "rule_id": "require-hardhat-in-visitor-corridor",
            "camera_id": "CAM-06",
            "value": 0.42,
            "unit": "m",
            "speed": 1.65,
            "subject": "CAM-06:person:90e3b71c",
            "verdict": "violation",
            "score": 0.91,
            "reason": (
                "Two workers in West Assembly Zone (CAM-06) engage in rapid physical contact / "
                "shove while reviewing floor blueprints; supervisor alerted immediately."
            ),
            "action_kind": "notify",
            "action_role": "supervisor",
            "action_detail": "escalated high-priority floor alert to supervisor on CAM-06",
            "ack_role": "supervisor",
            "dispute": None,
        },
        {
            "event_id": "ev-7c178ff2fc3077ff611eb7e9",
            "rule_id": "commercial-kitchen-prep-sanitation-guard",
            "camera_id": "CAM-05",
            "value": 0.12,
            "unit": "m",
            "speed": 0.18,
            "subject": "CAM-05:person:22c8f401",
            "verdict": "no_violation",
            "score": 0.11,
            "reason": (
                "All 4 chefs on Prep Line A are wearing compliant white chef hats / head "
                "coverings while plating; initial shadow occlusion on station 3 resolved by "
                "Tier-0 crop-zoom."
            ),
            "action_kind": "notify",
            "action_role": "kitchen_lead",
            "action_detail": "cleared by Tier-1 Glimmer verifier (all head coverings present)",
            "ack_role": None,
            "dispute": (
                "glare_or_shadow",
                "Overhead hood shadow briefly occluded white chef cap.",
                "upheld",
            ),
        },
    ]

    for spec in incidents_spec:
        rule = rule_map[str(spec["rule_id"])]
        cam_id = str(spec["camera_id"])
        ev_id = str(spec["event_id"])
        t_end = clock.now()
        t_start = t_end - timedelta(seconds=3, milliseconds=200)
        strip_files = [
            str((strips_dir / f"{cam_id}_strip_{idx}.jpg").resolve())
            for idx in range(3)
            if (strips_dir / f"{cam_id}_strip_{idx}.jpg").is_file()
        ]
        cand = CandidateEvent(
            event_id=ev_id,
            rule_id=rule.id,
            rule_ir_hash=rule_ir_hash(rule),
            plan_version_hash=plan_vhash,
            camera_id=cam_id,
            ts_start=t_start,
            ts_end=t_end,
            severity=rule.severity,
            measurement=Measurement(value=float(spec["value"]), unit=str(spec["unit"])),
            predicate_trace={
                "all": True,
                "all.0.distance_m": True,
                "all.0.distance_m.value": float(spec["value"]),
                "all.1.moving": True,
                "all.1.moving.value": float(spec["speed"]),
                "all.value": float(spec["value"]),
                "episode_s": 3.2,
                "subject": str(spec["subject"]),
            },
            track_window_hash=hashlib.sha256(ev_id.encode()).hexdigest(),
            strip_paths=tuple(strip_files),
        )
        ctx = DecisionContext(
            rule=rule,
            calibration=Calibration(
                camera_id=cam_id,
                homography=HOMOGRAPHY,
                method="reference_object",
                reference="pallet_48x40in",
                err_m=0.094,
            ),
            zones={},
            t0=t_end - timedelta(seconds=30),
            t1=t_end,
            hz=10.0,
            plan_version_hash=plan_vhash,
            operator_exclusion=True,
        )
        chain.append("candidate", candidate_payload(cand, ctx))

        verdict_obj = VerifierVerdict(
            verdict=spec["verdict"],  # type: ignore[arg-type]
            score=float(spec["score"]),
            reason=str(spec["reason"]),
            tier="glimmer",
            model_version="muse-glimmer-30b",
            latency_ms=412,
            tamper=False,
            scene_text=(
                f"1/3 t+0.0s {rule.id} measured {spec['value']:.2f} {spec['unit']}\nsubject: "
                f"{spec['subject']}"
            ),
        )
        v_payload = verdict_obj.model_dump(mode="json")
        v_payload["event_id"] = ev_id
        chain.append("verdict", v_payload)

        chain.append(
            "action",
            {
                "event_id": ev_id,
                "kind": spec["action_kind"],
                "role": spec["action_role"],
                "gate": "auto",
                "ok": True,
                "detail": spec["action_detail"],
                "approved_by_role": None,
                "ts": clock.now().isoformat().replace("+00:00", "Z"),
            },
        )

        if spec["ack_role"]:
            chain.append(
                "ack",
                {
                    "event_id": ev_id,
                    "role": spec["ack_role"],
                    "action": "acknowledged",
                },
            )

        if spec["dispute"] is not None:
            reason_choice, reason_text, outcome = spec["dispute"]
            site_log = read_site_log(chain, today, 30)
            logged_ev = site_log.events.get(ev_id)
            if logged_ev is not None:
                disp = Dispute(
                    channel="tap",
                    reason_text=f"{reason_choice}: {reason_text}",
                    outcome=outcome,  # type: ignore[arg-type]
                    reviewer_reasoning=f"Tier-2 Spark Open upheld worker dispute: {reason_text}",
                )
                chain.append(
                    "dispute",
                    dispute_payload(f"disp-{ev_id[-8:]}", logged_ev, disp),
                )

    # Verify today's chain and commit its Merkle root
    report = verify_chain(today_path)
    if not report.ok:
        raise RuntimeError(
            f"Chain verification failed at seq {report.first_bad_seq}: {report.reason}"
        )
    root_hex, n_rec = chain_root(today_path)
    (ROOTS_DIR / f"{today.isoformat()}.txt").write_text(
        f"demo {root_hex} {n_rec}\n", encoding="utf-8"
    )
    print(
        f"Seeded {n_rec} cryptographically verified records into {today_path} "
        f"(root={root_hex[:16]}...)"
    )


def main() -> None:
    """Seed videos, chunks, plans and chains, then post the plans to a running API if any."""
    now = datetime.now(UTC)
    cam_paths = prepare_videos_and_strips()
    seed_chunks(cam_paths, now)
    plans = build_plans()
    seed_chains_and_activation(plans, now)
    try:
        import httpx  # noqa: PLC0415 - only needed when an API is running

        for p in plans:
            httpx.post("http://localhost:8000/plans", json=p.model_dump(mode="json"), timeout=3.0)
        httpx.post(f"http://localhost:8000/plans/{plans[-1].plan_id}/approve", timeout=3.0)
    except Exception:  # noqa: S110 - no API running is fine: the seeded files are the output
        pass
    print("Successfully seeded live backend data, plans, chunks, strips, and chains.")


if __name__ == "__main__":
    main()
