"""The worker page lists a zone-less rule on the cameras it runs on (recorded T02 plan)."""

from __future__ import annotations

import json
from pathlib import Path

from rulecam.contracts import SitePlan
from rulecam.worker.page import camera_rules

PLAN = Path(__file__).resolve().parents[2] / "scenarios" / "worker_passes_4m" / "plan.json"


def test_forklift_rule_without_zones_is_on_its_camera_page() -> None:
    plan = SitePlan.model_validate(json.loads(PLAN.read_text(encoding="utf-8")))
    assert all(not rule.zones for rule in plan.rules)
    assert [r.id for r in camera_rules(plan, "phone-97")] == ["keep-people-4m-from-moving-forklift"]
    assert camera_rules(plan, "another-camera") == ()
