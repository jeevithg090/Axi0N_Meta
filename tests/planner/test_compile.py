"""Tests for rule compiler and repair round (task P2-03, MP §7.3, V2 §6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypeVar

import pytest
import yaml
from pydantic import BaseModel

from rulecam.contracts.plan import InventoryItem
from rulecam.contracts.rule_ir import ConceptSpec, RuleIR, ZoneSpec
from rulecam.planner.errors import GuardrailDeclinedError, RuleCompilationError
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE, Concern
from rulecam.planner.steps.compile import (
    _COMPILE_SYSTEM_PROMPT,
    IntermediatePredicate,
    _ensure_rule_ir,
    apply_static_hints,
    compile_rule,
    convert_intermediate_predicate,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"
M = TypeVar("M", bound=BaseModel)


class MockSparkLLM:
    """Mock StructuredLLM for compiler tests supporting sequenced responses and exceptions."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
    ) -> M:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "schema": schema,
                "reasoning_effort": reasoning_effort,
                "background": background,
            }
        )
        if not self._responses:
            msg = "MockSparkLLM ran out of configured responses"
            raise RuntimeError(msg)
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, RuleIR):
            return resp  # type: ignore[return-value]
        if isinstance(resp, dict):
            return schema.model_validate(resp)
        return resp  # type: ignore[return-value]


def _load_fixture(name: str) -> RuleIR:
    raw = yaml.safe_load((FIXTURES_DIR / f"{name}.rule.yaml").read_text(encoding="utf-8"))
    return RuleIR.model_validate(raw)


def test_compile_handwash_fixture() -> None:
    """Compiling the handwash concern returns IR equal to wash-before-line fixture."""
    expected = _load_fixture("wash-before-line")
    spark = MockSparkLLM([expected])

    concern = Concern(
        text="Everyone entering the line must wash hands for at least 20 seconds",
        source_ref="plan",
        standard_ref="Food Code 2026 §2-301.12",
    )
    compiled = compile_rule(
        concern,
        spark=spark,
        plan_id="main-st-kitchen",
        zones=expected.zones,
    )

    assert compiled == expected
    assert compiled.predicate.op == "sequence"
    assert compiled.concepts["sink"].static is True


def test_compile_excavator_swing_radius_fixture() -> None:
    """Compiling excavator request returns IR equal to excavator-swing-radius fixture."""
    expected = _load_fixture("excavator-swing-radius")
    spark = MockSparkLLM([expected])

    concern = Concern(
        text="avísame si alguien está a menos de dos metros del balde de la excavadora",
        source_ref="voice",
    )
    compiled = compile_rule(
        concern,
        spark=spark,
        calibration="ground-plane@cam1",
    )

    assert compiled == expected
    assert compiled.predicate.op == "persist"
    assert compiled.severity == "high"


def test_compile_fire_exit_blocked_fixture() -> None:
    """Compiling fire exit sign returns IR equal to fire-exit-blocked fixture."""
    expected = _load_fixture("fire-exit-blocked")
    spark = MockSparkLLM([expected])

    concern = Concern(
        text="KEEP CLEAR — FIRE EXIT",
        source_ref="sign_photo",
    )
    compiled = compile_rule(
        concern,
        spark=spark,
        calibration="ground-plane@cam1",
        zones=expected.zones,
    )

    assert compiled == expected
    assert compiled.concepts["door"].static is True
    assert compiled.predicate.op == "persist"


def test_compile_with_full_context_lines() -> None:
    """Compiling with site_name, zones, inventory formats user prompt completely."""
    expected = _load_fixture("fire-exit-blocked")
    spark = MockSparkLLM([expected])

    zones = {"egress": ZoneSpec(kind="buffer", camera_id="cam1", buffer_of="door", buffer_m=1.5)}
    inventory = [InventoryItem(concept="door", camera_id="cam1", count=1, mean_confidence=0.9)]

    compiled = compile_rule(
        "Clear fire exit",
        spark=spark,
        site_name="Warehouse A",
        zones=zones,
        inventory=inventory,
        calibration="cam1-cal",
    )
    assert compiled == expected
    user_prompt = str(spark.calls[0]["messages"])
    assert "Warehouse A" in user_prompt
    assert "egress" in user_prompt
    assert "cam1-cal" in user_prompt


def test_repair_round_on_invalid_first_output() -> None:
    """Invalid model output on attempt 1 triggers one repair round with error attached."""
    expected = _load_fixture("wash-before-line")
    validation_error = ValueError("Missing required predicate op")

    spark = MockSparkLLM([validation_error, expected])

    concern = "Everyone entering the line must wash hands for at least 20 seconds"
    compiled = compile_rule(concern, spark=spark, plan_id="main-st-kitchen")

    assert compiled == expected
    assert len(spark.calls) == 2

    second_call_messages = spark.calls[1]["messages"]
    assert isinstance(second_call_messages, list)
    repair_msg = str(second_call_messages[-1])
    assert "Missing required predicate op" in repair_msg
    assert "Please fix all schema errors" in repair_msg


def test_repair_failure_raises_rule_compilation_error() -> None:
    """Persistent failure across repair round raises RuleCompilationError."""
    err1 = ValueError("First parse error")
    err2 = ValueError("Second parse error after repair")

    spark = MockSparkLLM([err1, err2])

    with pytest.raises(RuleCompilationError, match="Rule compilation failed after repair round"):
        compile_rule("Check something", spark=spark)

    assert len(spark.calls) == 2


def test_guardrail_decline_prevents_compilation() -> None:
    """A banned request raises GuardrailDeclinedError immediately without calling LLM."""
    spark = MockSparkLLM([])

    with pytest.raises(
        GuardrailDeclinedError, match="Request declined under per_person_productivity"
    ):
        compile_rule("who is slowest", spark=spark)

    assert len(spark.calls) == 0


def test_apply_static_hints_from_inventory() -> None:
    """Concepts matching static objects in inventory are tagged static=True."""
    rule = _load_fixture("wash-before-line")
    rule_non_static = rule.model_copy(
        update={"concepts": {**rule.concepts, "sink": ConceptSpec(prompt="sink", static=False)}}
    )
    assert rule_non_static.concepts["sink"].static is False

    inventory = [
        InventoryItem(concept="sink", camera_id="cam1", count=1, mean_confidence=0.95),
        InventoryItem(concept="person", camera_id="cam1", count=2, mean_confidence=0.88),
    ]

    tagged_rule = apply_static_hints(rule_non_static, inventory=inventory)
    assert tagged_rule.concepts["sink"].static is True
    assert tagged_rule.concepts["person"].static is False


def test_intermediate_predicate_conversions() -> None:
    """IntermediatePredicate converts all supported spatial, state, and logical predicates."""
    # Spatial: distance, inside, overlap, without
    p1 = convert_intermediate_predicate({"op": "distance_m", "a": "person", "b": "cart", "lt": 1.5})
    assert p1.op == "distance_m"

    p2 = convert_intermediate_predicate({"op": "inside", "object": "person", "zone": "hazard"})
    assert p2.op == "inside"

    p3 = convert_intermediate_predicate({"op": "overlap", "a": "box", "b": "shelf", "min_iou": 0.2})
    assert p3.op == "overlap"

    p4 = convert_intermediate_predicate(
        {"op": "without", "subject": "worker", "item": "helmet", "region": "top_25pct"}
    )
    assert p4.op == "without"

    # State / motion: moving, count, pose, state
    p5 = convert_intermediate_predicate({"op": "moving", "object": "forklift", "speed_mps_gt": 1.0})
    assert p5.op == "moving"

    p6 = convert_intermediate_predicate(
        {"op": "count", "object": "person", "zone": "dock", "gt": 3}
    )
    assert p6.op == "count"

    p7 = convert_intermediate_predicate({"op": "pose", "object": "person", "state": "down"})
    assert p7.op == "pose"

    p8 = convert_intermediate_predicate({"op": "state", "object": "door", "state": "open"})
    assert p8.op == "state"

    # Logical: all, any, not
    p9 = convert_intermediate_predicate(
        {"op": "all", "of": [{"op": "inside", "object": "a", "zone": "z"}]}
    )
    assert p9.op == "all"

    p10 = convert_intermediate_predicate(
        {"op": "any", "of": [{"op": "inside", "object": "a", "zone": "z"}]}
    )
    assert p10.op == "any"

    p11 = convert_intermediate_predicate(
        {"op": "not", "of": {"op": "inside", "object": "a", "zone": "z"}}
    )
    assert p11.op == "not"

    # Temporal: persist, dwell, absence, rate, sequence
    p12 = convert_intermediate_predicate(
        {"op": "dwell", "object": "hands", "zone": "sink", "total_s_gte": 20.0}
    )
    assert p12.op == "dwell"

    p13 = convert_intermediate_predicate(
        {"op": "absence", "object": "guard", "zone": "gate", "seconds": 60.0}
    )
    assert p13.op == "absence"

    p14 = convert_intermediate_predicate(
        {"op": "rate", "of": {"op": "inside", "object": "a", "zone": "z"}, "per_minute_gt": 5.0}
    )
    assert p14.op == "rate"

    seq = convert_intermediate_predicate(
        IntermediatePredicate(
            op="sequence",
            semantics="future",
            require_within_s=45.0,
            trigger=IntermediatePredicate(op="inside", object="p", zone="z"),
            requirement=IntermediatePredicate(op="dwell", object="p", zone="z", total_s_gte=10.0),
        )
    )
    assert seq.op == "sequence"


def test_intermediate_predicate_errors() -> None:
    """Validation errors on malformed intermediate predicates."""
    with pytest.raises(ValueError, match="Not_ predicate missing 'of'"):
        convert_intermediate_predicate({"op": "not"})

    with pytest.raises(ValueError, match="Persist predicate missing 'of'"):
        convert_intermediate_predicate({"op": "persist"})

    with pytest.raises(ValueError, match="Rate predicate missing 'of'"):
        convert_intermediate_predicate({"op": "rate"})

    with pytest.raises(ValueError, match="Sequence predicate missing 'trigger'"):
        convert_intermediate_predicate({"op": "sequence"})

    with pytest.raises(ValueError, match="Unknown predicate op: invalid_op"):
        convert_intermediate_predicate({"op": "invalid_op"})


def test_ensure_rule_ir_from_dict() -> None:
    """_ensure_rule_ir converts a valid dict conforming to RuleIR."""
    rule = _load_fixture("wash-before-line")
    dumped = rule.model_dump(mode="json")
    reconstructed = _ensure_rule_ir(dumped)
    assert reconstructed == rule


def test_prompt_hygiene_clause_in_compile_system_prompt() -> None:
    """Mandatory data-not-instructions clause is present in the compile system prompt."""
    assert DATA_NOT_INSTRUCTIONS_CLAUSE in _COMPILE_SYSTEM_PROMPT
