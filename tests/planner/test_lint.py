"""The plan linter (task P2-06b): one test per error class, plus the clean case. No network."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.plan import AcceptanceResult, SitePlan
from rulecam.contracts.rule_ir import (
    Absence,
    All,
    ConceptSpec,
    Count,
    DistanceM,
    Dwell,
    Inside,
    Not_,
    Persist,
    RuleIR,
    ZoneSpec,
)
from rulecam.planner.lint import (
    CameraViews,
    LintFinding,
    PlanLintError,
    lint_plan,
    lint_rule,
    require_lint_clean,
)

RULES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"
VIEWS: CameraViews = {"cam1": (640, 480)}


def _rule(**update: object) -> RuleIR:
    raw = yaml.safe_load((RULES_DIR / "wash-before-line.rule.yaml").read_text(encoding="utf-8"))
    return RuleIR.model_validate({**raw, **update})


def _plan(*rules: RuleIR, privacy_mode: PrivacyMode = PrivacyMode.STANDARD) -> SitePlan:
    return SitePlan(
        plan_id="lint",
        site_name="Main St",
        setting_inferred="commercial_kitchen",
        languages=("en",),
        request=(),
        inventory=(),
        feasibility=(),
        rules=rules,
        privacy_mode=privacy_mode,
        acceptance=AcceptanceResult(checks=(), rounds=0),
    )


def _codes(findings: tuple[LintFinding, ...] | list[LintFinding]) -> list[str]:
    return [f.code for f in findings]


def test_the_handwash_rule_is_clean() -> None:
    plan = _plan(_rule(), privacy_mode=PrivacyMode.ANONYMOUS)
    assert lint_plan(plan, views=VIEWS) == ()
    require_lint_clean(plan, views=VIEWS)


def test_undefined_concept() -> None:
    rule = _rule(predicate=Inside(object="forklift.foot_point", zone="line_entry").model_dump())
    findings = lint_rule(rule, views=VIEWS)
    assert _codes(findings) == ["undefined_concept"]
    assert "'forklift.foot_point'" in findings[0].message


def test_a_buffer_of_an_undeclared_concept_is_an_undefined_concept() -> None:
    zones = {"near_fryer": {"kind": "buffer", "camera_id": "cam1", "buffer_of": "fryer"}}
    rule = _rule(zones=zones, predicate=Inside(object="person", zone="near_fryer").model_dump())
    assert _codes(lint_rule(rule, views=VIEWS)) == ["undefined_concept"]


def test_undefined_zone() -> None:
    pred = Count(object="person", zone="dock", gt=3)
    findings = lint_rule(_rule(predicate=pred.model_dump()), views=VIEWS)
    assert _codes(findings) == ["undefined_zone"]
    assert "'dock'" in findings[0].message


@pytest.mark.parametrize(
    ("views", "expected"),
    [
        ({"cam2": (640, 480)}, "which the site does not have"),
        ({"cam1": (300, 200)}, None),  # partly in view: not a lint error
        ({"cam1": None}, None),  # frame size unknown: only the camera is checked
    ],
)
def test_zone_out_of_view_by_camera(views: CameraViews, expected: str | None) -> None:
    findings = lint_rule(_rule(), views=views)
    if expected is None:
        assert findings == ()
    else:
        assert _codes(findings) == ["zone_out_of_view", "zone_out_of_view"]
        assert expected in findings[0].message


def test_zone_out_of_view_by_polygon() -> None:
    far = {
        "kind": "polygon",
        "camera_id": "cam1",
        "polygon_px": [[900, 900], [990, 900], [990, 990]],
    }
    rule = _rule(zones={**_rule().model_dump()["zones"], "line_entry": far})
    findings = lint_rule(rule, views=VIEWS)
    assert _codes(findings) == ["zone_out_of_view"]
    assert "outside the 640x480 frame" in findings[0].message


@pytest.mark.parametrize(
    "predicate",
    [
        DistanceM(a="person", b="sink", lt=0.1),
        DistanceM(a="person", b="sink", gt=25.0),
        Persist(of=Inside(object="person", zone="line_entry"), seconds=0.5),
        Dwell(object="person", zone="line_entry", total_s_gte=5000.0),
        Absence(object="person", zone="line_entry", seconds=0.0),
    ],
)
def test_unreasonable_threshold(predicate: object) -> None:
    rule = _rule(predicate=predicate.model_dump())  # type: ignore[attr-defined]
    assert _codes(lint_rule(rule, views=VIEWS)) == ["unreasonable_threshold"]


def test_the_threshold_bounds_are_inclusive_and_cover_buffers() -> None:
    ok = DistanceM(a="person", b="sink", lt=0.3)
    assert lint_rule(_rule(predicate=ok.model_dump()), views=VIEWS) == ()
    zones = _rule().model_dump()["zones"]
    zones["sink_zone"]["buffer_m"] = 25.0
    findings = lint_rule(_rule(zones=zones), views=VIEWS)
    assert _codes(findings) == ["unreasonable_threshold"]
    assert "buffer_m" in findings[0].message


def test_contradictory_conditions_within_one_rule() -> None:
    inside = Inside(object="person", zone="line_entry")
    pred = All(of=(inside, Not_(of=inside)))
    assert _codes(lint_rule(_rule(predicate=pred.model_dump()), views=VIEWS)) == [
        "contradictory_rules"
    ]


def test_contradictory_rules_across_the_plan() -> None:
    keep_out = _rule(
        id="keep-out", predicate=Inside(object="person", zone="line_entry").model_dump()
    )
    staffed = _rule(
        id="staffed",
        predicate=Absence(object="person", zone="line_entry", seconds=60).model_dump(),
    )
    unrelated = _rule(
        id="other", predicate=Absence(object="hands", zone="line_entry", seconds=60).model_dump()
    )
    findings = lint_plan(_plan(keep_out, unrelated, staffed), views=VIEWS)
    assert _codes(findings) == ["contradictory_rules"]
    assert findings[0].rule_id == "staffed"
    assert "'keep-out' and 'staffed'" in findings[0].message


@pytest.mark.parametrize("mode", [PrivacyMode.ANONYMOUS, PrivacyMode.STRICT])
def test_privacy_inconsistency(mode: PrivacyMode) -> None:
    concepts = {**_rule().model_dump()["concepts"], "mask": {"prompt": "face mask"}}
    rule = _rule(concepts=concepts)
    findings = lint_rule(rule, views=VIEWS, privacy_mode=mode)
    assert _codes(findings) == ["privacy_inconsistency"]
    assert lint_rule(rule, views=VIEWS, privacy_mode=PrivacyMode.STANDARD) == ()


@pytest.mark.parametrize("name", ["staff_restroom", "Break-Room-2", "locker room", "banos"])
def test_private_space_zone(name: str) -> None:
    zones = {
        **_rule().model_dump()["zones"],
        name: {"kind": "buffer", "camera_id": "cam1", "buffer_of": "sink", "buffer_m": 1},
    }
    findings = lint_rule(_rule(zones=zones), views=VIEWS)
    assert _codes(findings) == ["private_space_zone"]


@pytest.mark.parametrize("name", ["eyewash_station", "safety_shower_station", "dish_room"])
def test_ordinary_zone_names_are_not_private_spaces(name: str) -> None:
    zones = {
        **_rule().model_dump()["zones"],
        name: {"kind": "buffer", "camera_id": "cam1", "buffer_of": "sink", "buffer_m": 1},
    }
    assert lint_rule(_rule(zones=zones), views=VIEWS) == ()


def test_missing_action() -> None:
    assert _codes(lint_rule(_rule(actions=[]), views=VIEWS)) == ["missing_action"]


def test_missing_severity() -> None:
    valid = _rule()
    fields = {k: v for k, v in valid if k != "severity"}
    unvalidated = RuleIR.model_construct(**fields)  # e.g. a hand-written rule loaded without checks
    assert _codes(lint_rule(unvalidated, views=VIEWS)) == ["missing_severity"]


def test_require_lint_clean_raises_with_every_finding() -> None:
    bad = _rule(actions=[], predicate=Inside(object="ghost", zone="nowhere").model_dump())
    with pytest.raises(PlanLintError) as err:
        require_lint_clean(_plan(bad), views=VIEWS)
    assert _codes(err.value.findings) == ["undefined_concept", "undefined_zone", "missing_action"]


def test_object_parts_resolve_to_their_concept() -> None:
    concepts = {"person": ConceptSpec(prompt="person").model_dump()}
    rule = _rule(
        concepts=concepts,
        zones={
            "line_entry": ZoneSpec(
                kind="polygon", camera_id="cam1", polygon_px=((0, 0), (10, 0), (10, 10))
            ).model_dump()
        },
        predicate=Inside(object="person.hands", zone="line_entry").model_dump(),
    )
    assert lint_rule(rule, views=VIEWS) == ()
