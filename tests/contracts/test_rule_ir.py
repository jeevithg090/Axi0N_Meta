from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from rulecam.contracts import RuleIR, Severity
from rulecam.contracts.rule_ir import (
    Absence,
    All,
    Any_,
    Count,
    DistanceM,
    Dwell,
    Inside,
    Moving,
    Not_,
    Overlap,
    Persist,
    PoseState,
    Rate,
    Sequence,
    StateCheck,
    Without,
)


def load_handwash(fixtures_dir: Path) -> RuleIR:
    data = yaml.safe_load((fixtures_dir / "rules" / "wash-before-line.rule.yaml").read_text())
    return RuleIR.model_validate(data)


def test_handwash_rule_parses_into_typed_ir(fixtures_dir: Path) -> None:
    rule = load_handwash(fixtures_dir)

    assert rule.severity is Severity.MEDIUM
    assert isinstance(rule.predicate, Sequence)
    assert rule.predicate.semantics == "past"
    assert isinstance(rule.predicate.trigger, Inside)
    requirement = rule.predicate.requirement
    assert isinstance(requirement, Dwell)
    assert requirement.total_s_gte == 20
    assert requirement.max_gap_s == 3
    assert rule.concepts["sink"].static is True
    assert rule.backtestable == "yes"


def test_handwash_rule_round_trips_through_json(fixtures_dir: Path) -> None:
    rule = load_handwash(fixtures_dir)
    assert RuleIR.model_validate_json(rule.model_dump_json()) == rule


def test_every_predicate_op_round_trips() -> None:
    leaves = (
        DistanceM(a="person.foot_point", b="bucket.mask", lt=2.0),
        Inside(object="person", zone="aisle"),
        Overlap(a="hands", b="faucet", min_iou=0.1),
        Without(subject="person", item="hard hat", region="top_25pct"),
        Moving(object="forklift", speed_mps_gt=0.2),
        Count(object="box", zone="aisle", gt=3),
        PoseState(state="down"),
        StateCheck(object="door", state="open"),
        Dwell(object="hands", zone="sink", total_s_gte=20, max_gap_s=3),
        Absence(object="staff", zone="vestibule", seconds=20),
    )
    composite = All(
        of=(
            *leaves,
            Any_(of=(Inside(object="a", zone="b"),)),
            Not_(of=Inside(object="a", zone="b")),
            Persist(of=Inside(object="a", zone="b"), seconds=1.0),
            Rate(of=Inside(object="a", zone="b"), per_minute_gt=2),
            Sequence(
                semantics="future",
                trigger=Inside(object="a", zone="b"),
                require_within_s=60,
                requirement=Inside(object="a", zone="c"),
            ),
        )
    )
    rule = RuleIR.model_validate(
        {
            "id": "every-op",
            "source": {"kind": "text", "text": "test"},
            "concepts": {"person": {"prompt": "person"}},
            "predicate": composite.model_dump(),
            "severity": "low",
            "actions": [{"kind": "log"}],
        }
    )

    assert RuleIR.model_validate_json(rule.model_dump_json()) == rule
    assert isinstance(rule.predicate, All)
    assert {type(p).__name__ for p in rule.predicate.of} >= {
        "DistanceM",
        "Any_",
        "Not_",
        "Sequence",
    }


def _minimal_rule(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "r",
        "source": {"kind": "text"},
        "concepts": {"person": {"prompt": "person"}},
        "predicate": {"op": "inside", "object": "person", "zone": "z"},
        "severity": "high",
        "actions": [{"kind": "log"}],
    }
    base.update(overrides)
    return base


def test_unknown_predicate_op_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RuleIR.model_validate(_minimal_rule(predicate={"op": "teleport", "object": "x"}))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RuleIR.model_validate(_minimal_rule(colour="red"))


def test_rules_are_immutable() -> None:
    rule = RuleIR.model_validate(_minimal_rule())
    with pytest.raises(ValidationError):
        rule.id = "changed"


def test_persist_requires_positive_seconds() -> None:
    with pytest.raises(ValidationError):
        Persist(of=Inside(object="a", zone="b"), seconds=0)


def test_defaults_are_not_shared_between_instances() -> None:
    first = RuleIR.model_validate(_minimal_rule())
    second = RuleIR.model_validate(_minimal_rule(id="r2"))
    assert first.zones == {}
    assert first.zones is not second.zones
    assert first.verify.tier2 == "spark-open-on-disagree"
