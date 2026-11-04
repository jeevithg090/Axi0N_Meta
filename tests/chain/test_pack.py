"""Rule packs: loading, lint (guardrails included) and Rule CI across every knob's range."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from rulecam.chain import PackError, lint_pack, load_pack, parse_pack, prove_pack
from rulecam.chain.models import Knob, Slot
from rulecam.chain.pack import knob_settings, names_private_space, reference_rule
from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.rule_ir import Dwell, Sequence
from tests.chain.helpers import DEMO, demo_pack_data, handwash_only, pack_from


def _codes(data: dict[str, Any]) -> list[str]:
    return [f.code for f in lint_pack(pack_from(data))]


def _handwash() -> dict[str, Any]:
    template: dict[str, Any] = demo_pack_data()["templates"][0]
    return template


# --- the demo pack ---------------------------------------------------------------------------


def test_the_demo_pack_is_releasable_at_every_knob_setting() -> None:
    proof = prove_pack(load_pack(DEMO / "pack.yaml"))

    assert proof.findings == ()
    assert proof.releasable
    labels = {t.rule_id: [s.label for s in t.settings] for t in proof.templates}
    assert labels == {
        "handwash-before-line": ["default", "wash_seconds=40", "wash_window=120"],
        "fire-exit-clear": ["default", "blocked_seconds=30"],
        "delivery-to-cold-storage": ["default", "dock_seconds=300"],
    }
    assert all(s.ci_green for t in proof.templates for s in t.settings)


def test_the_digest_changes_with_any_edit_and_not_otherwise() -> None:
    data = demo_pack_data()
    assert pack_from(data).digest() == pack_from(data).digest()
    data["templates"][0]["knobs"][0]["high"] = 45
    assert pack_from(data).digest() != pack_from(demo_pack_data()).digest()


def test_knob_settings_skip_ends_equal_to_the_default() -> None:
    template = pack_from(handwash_only()).templates[0]
    settings = dict(knob_settings(template))
    assert settings["default"] == {"wash_seconds": 20.0, "wash_window": 300.0}
    assert settings["wash_seconds=40"] == {"wash_seconds": 40.0, "wash_window": 300.0}
    assert "wash_seconds=20" not in settings


def test_the_reference_fit_lays_slots_on_the_hq_camera() -> None:
    template = pack_from(handwash_only()).templates[0]
    rule = reference_rule(template, {"wash_seconds": 30.0})
    assert {z.camera_id for z in rule.zones.values()} == {"hq-ref"}
    assert rule.zones["sink_zone"].kind == "buffer"
    assert rule.calibration == "ground-plane@hq-ref"
    assert isinstance(rule.predicate, Sequence)
    assert isinstance(rule.predicate.requirement, Dwell)
    assert rule.predicate.requirement.total_s_gte == 30.0


# --- lint and guardrails ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "concern",
    [
        "Tell me who is slowest on the line every shift.",
        "Recognise faces of the cooks when they clock in.",
        "Tell me if the cook looks sick.",
    ],
)
def test_a_concern_the_planner_would_decline_stops_the_pack(concern: str) -> None:
    template = _handwash()
    template["concern"] = concern
    assert "declined_concern" in _codes(handwash_only(templates=[template]))


@pytest.mark.parametrize("slot", ["staff_break_room", "bathroom-door", "locker.room"])
def test_a_slot_named_after_a_private_space_stops_the_pack(slot: str) -> None:
    template = _handwash()
    template["slots"][slot] = template["slots"].pop("line_entry")
    template["rule"]["predicate"]["trigger"]["zone"] = slot
    assert _codes(handwash_only(templates=[template])) == ["private_space_slot"]


def test_a_slot_described_as_a_private_space_stops_the_pack() -> None:
    template = _handwash()
    template["slots"]["line_entry"]["describe"] = "The staff break room by the line"
    assert _codes(handwash_only(templates=[template])) == ["private_space_slot"]


def test_names_private_space_allows_ordinary_zones() -> None:
    assert not names_private_space("line_entry")
    assert not names_private_space("safety shower")
    assert names_private_space("staff_break_room")


def test_slot_problems_are_found() -> None:
    template = _handwash()
    template["rule"]["zones"] = {"x": {"kind": "polygon", "camera_id": "c", "polygon_px": []}}
    template["rule"]["predicate"]["trigger"]["zone"] = "nowhere"
    template["slots"]["sink_zone"]["buffer_of"] = "tap"
    codes = _codes(handwash_only(templates=[template]))
    assert codes == ["template_zones", "undefined_slot", "unused_slot", "buffer_concept"]


def test_camera_slots_are_not_zones() -> None:
    template = _handwash()
    template["slots"]["line_cam"] = {"kind": "camera", "describe": "The camera on the line"}
    assert _codes(handwash_only(templates=[template])) == []


@pytest.mark.parametrize(
    ("knob", "message"),
    [
        ({"path": ["requirement", "nope"]}, "does not exist"),
        ({"path": ["requirement", "op"]}, "not a number"),
        ({"low": 25}, "outside"),
    ],
)
def test_broken_knobs_are_found(knob: dict[str, Any], message: str) -> None:
    template = _handwash()
    template["knobs"][0].update(knob)
    findings = lint_pack(pack_from(handwash_only(templates=[template])))
    assert [f.code for f in findings] == ["bad_knob"]
    assert message in findings[0].message


def test_a_knob_defined_twice_is_found() -> None:
    template = _handwash()
    template["knobs"].append(dict(template["knobs"][0]))
    assert "bad_knob" in _codes(handwash_only(templates=[template]))


def test_face_concepts_need_standard_privacy() -> None:
    template = _handwash()
    template["rule"]["concepts"]["face"] = {"prompt": "face"}
    assert _codes(handwash_only(templates=[template])) == []
    data = handwash_only(templates=[template], privacy_floor="anonymous")
    assert _codes(data) == ["privacy_inconsistency"]


def test_rules_need_actions_and_unique_ids() -> None:
    template = _handwash()
    template["rule"]["actions"] = []
    data = handwash_only(templates=[template, _handwash()])
    assert _codes(data) == ["duplicate_rule", "missing_action"]


def test_a_template_with_findings_is_not_proved_and_the_pack_is_not_releasable() -> None:
    template = _handwash()
    template["rule"]["predicate"]["trigger"]["zone"] = "nowhere"
    proof = prove_pack(pack_from(handwash_only(templates=[template])))
    assert proof.templates == ()
    assert not proof.releasable


def test_a_rule_shape_rule_ci_cannot_attack_is_not_releasable() -> None:
    template = {
        "concern": "Tell me if anyone is down on the floor for 10 seconds.",
        "rule": {
            "id": "person-down",
            "source": {"kind": "plan"},
            "concepts": {"person": {"prompt": "person"}},
            "predicate": {
                "op": "persist",
                "seconds": 10,
                "of": {"op": "pose", "object": "person", "state": "down"},
            },
            "severity": "high",
            "actions": [{"kind": "notify", "role": "shift_lead"}],
        },
    }
    proof = prove_pack(pack_from(handwash_only(templates=[template])))
    assert not proof.releasable
    assert "no boundary traces" in proof.templates[0].settings[0].failures[0]


def test_a_knob_value_the_predicate_cannot_hold_fails_that_setting() -> None:
    template = _handwash()
    template["rule"]["predicate"] = {
        "op": "persist",
        "seconds": 30,
        "of": {"op": "count", "object": "person", "zone": "line_entry", "gt": 4},
    }
    template["slots"].pop("sink_zone")
    template["knobs"] = [
        {"name": "crowd", "path": ["of", "gt"], "low": 2, "high": 4.5, "stricter": "lower"}
    ]
    proof = prove_pack(pack_from(handwash_only(templates=[template])))
    failing = [s for s in proof.templates[0].settings if s.label == "crowd=4.5"]
    assert failing[0].summary == "not run"
    assert "do not fit" in failing[0].failures[0]


# --- models and loading ----------------------------------------------------------------------


def test_slot_fields_must_match_the_kind() -> None:
    with pytest.raises(ValidationError, match="buffer slot needs"):
        Slot(kind="buffer", describe="sink")
    with pytest.raises(ValidationError, match="buffer slot needs"):
        Slot(kind="polygon", describe="line", buffer_of="sink", buffer_m=1.0)


def test_a_knob_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="is above high"):
        Knob(name="k", path=("seconds",), low=5, high=1, stricter="lower")


def test_parse_pack_reports_bad_files(tmp_path: Any) -> None:
    with pytest.raises(PackError, match="not valid YAML"):
        parse_pack("a: [")
    with pytest.raises(PackError, match="mapping"):
        parse_pack("- 1")
    with pytest.raises(PackError, match="invalid"):
        parse_pack("pack_id: x")
    with pytest.raises(PackError, match="cannot read"):
        load_pack(tmp_path / "missing.yaml")


def test_the_privacy_floor_defaults_to_standard() -> None:
    assert pack_from(handwash_only()).privacy_floor is PrivacyMode.STANDARD
