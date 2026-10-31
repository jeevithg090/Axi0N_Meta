"""Tests for the rule compiler and its repair round (task P2-03, MP §7.3, V2 §6). No network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypeVar

import pytest
import yaml
from pydantic import BaseModel

from rulecam.clients.errors import ModelHTTPError, StructuredOutputError
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.contracts.plan import InventoryItem
from rulecam.contracts.rule_ir import ConceptSpec, RuleIR, ZoneSpec
from rulecam.planner.errors import GuardrailDeclinedError, RuleCompilationError
from rulecam.planner.guardrails import GuardrailCategory, _ModelClassification
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE, Concern
from rulecam.planner.steps.compile import (
    _COMPILE_SYSTEM_PROMPT,
    PredicateNode,
    RuleDraft,
    apply_static_hints,
    compile_rule,
    draft_to_rule,
    reference_errors,
    rule_to_draft,
)
from tests.clients.meta_hosted.transport_fakes import ScriptedTransport
from tests.fakes import FakeClock

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"
M = TypeVar("M", bound=BaseModel)


class MockSparkLLM:
    """A `StructuredLLM` fake: guardrail calls get `classification`, compile calls get the queue.

    Queued drafts are sent through JSON and back, as the real client would parse them.
    """

    def __init__(
        self,
        responses: list[Any],
        classification: _ModelClassification | None = None,
    ) -> None:
        self._responses = list(responses)
        self._classification = classification or _ModelClassification(verdict="allowed")
        self.calls: list[dict[str, object]] = []

    @property
    def compile_calls(self) -> list[dict[str, object]]:
        return [c for c in self.calls if c["schema"] is RuleDraft]

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
    ) -> M:
        self.calls.append({"model": model, "messages": messages, "schema": schema})
        if schema is _ModelClassification:
            return schema.model_validate(self._classification.model_dump())
        assert schema is RuleDraft
        if not self._responses:
            msg = "MockSparkLLM ran out of configured responses"
            raise AssertionError(msg)
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        assert isinstance(resp, RuleDraft)
        return schema.model_validate_json(resp.model_dump_json())


def _message(spark: MockSparkLLM, call: int, index: int) -> str:
    messages = spark.compile_calls[call]["messages"]
    assert isinstance(messages, list)
    return str(messages[index])


def _load_fixture(name: str) -> RuleIR:
    raw = yaml.safe_load((FIXTURES_DIR / f"{name}.rule.yaml").read_text(encoding="utf-8"))
    return RuleIR.model_validate(raw)


def _draft(name: str) -> RuleDraft:
    return rule_to_draft(_load_fixture(name))


@pytest.mark.parametrize(
    "name", ["wash-before-line", "excavator-swing-radius", "fire-exit-blocked", "person-down"]
)
def test_draft_round_trips_every_fixture(name: str) -> None:
    rule = _load_fixture(name)
    draft = RuleDraft.model_validate_json(rule_to_draft(rule).model_dump_json())
    assert draft_to_rule(draft) == rule


def test_model_schema_has_no_recursive_refs() -> None:
    """The hosted API rejects $ref cycles (HTTP 400), so the draft schema must be acyclic."""
    schema = RuleDraft.model_json_schema()
    defs = schema.get("$defs", {})

    def refs(node: object) -> set[str]:
        if isinstance(node, dict):
            found = {node["$ref"].rsplit("/", 1)[-1]} if "$ref" in node else set()
            for value in node.values():
                found |= refs(value)
            return found
        if isinstance(node, list):
            return set().union(*(refs(v) for v in node)) if node else set()
        return set()

    def reaches(start: str, target: str, seen: frozenset[str] = frozenset()) -> bool:
        nxt = refs(defs[start])
        return target in nxt or any(reaches(n, target, seen | {start}) for n in nxt - seen)

    assert all(not reaches(name, name) for name in defs)


def test_compile_handwash_fixture() -> None:
    expected = _load_fixture("wash-before-line")
    spark = MockSparkLLM([_draft("wash-before-line")])
    concern = Concern(
        text="Everyone entering the line must wash hands for at least 20 seconds",
        source_ref="plan",
        standard_ref="Food Code 2026 §2-301.12",
    )

    compiled = compile_rule(concern, spark=spark, plan_id="main-st-kitchen", zones=expected.zones)

    assert compiled == expected
    assert compiled.predicate.op == "sequence"
    assert compiled.concepts["sink"].static is True
    assert [c["schema"] for c in spark.calls] == [_ModelClassification, RuleDraft]
    assert "Food Code 2026 §2-301.12" in str(spark.compile_calls[0]["messages"])


def test_compile_excavator_swing_radius_fixture() -> None:
    expected = _load_fixture("excavator-swing-radius")
    spark = MockSparkLLM([_draft("excavator-swing-radius")])
    concern = Concern(
        text="avísame si alguien está a menos de dos metros del balde de la excavadora",
        source_ref="voice",
    )

    compiled = compile_rule(concern, spark=spark, calibration="ground-plane@cam1")

    assert compiled == expected
    assert compiled.predicate.op == "persist"
    assert compiled.severity == "high"


def test_compile_fire_exit_blocked_fixture() -> None:
    expected = _load_fixture("fire-exit-blocked")
    spark = MockSparkLLM([_draft("fire-exit-blocked")])

    compiled = compile_rule(
        Concern(text="KEEP CLEAR — FIRE EXIT", source_ref="sign_photo"),
        spark=spark,
        calibration="ground-plane@cam1",
        zones=expected.zones,
    )

    assert compiled == expected
    assert compiled.concepts["door"].static is True


def test_compile_through_the_real_spark_client() -> None:
    """End to end: the request carries the RuleDraft schema and the reply becomes RuleIR."""
    expected = _load_fixture("fire-exit-blocked")

    def reply(text: str) -> dict[str, object]:
        content = [{"type": "output_text", "text": text}]
        return {
            "status": 200,
            "headers": {},
            "json": {"status": "completed", "output": [{"type": "message", "content": content}]},
        }

    transport = ScriptedTransport(
        reply(json.dumps({"verdict": "allowed"})), reply(rule_to_draft(expected).model_dump_json())
    )
    compiled = compile_rule(
        "Keep the fire exit clear", spark=SparkClient(transport, clock=FakeClock())
    )

    assert compiled == expected
    fmt = transport.bodies()[1]["text"]["format"]
    assert fmt["name"] == "RuleDraft"
    assert "predicate_nodes" in fmt["schema"]["properties"]


def test_compile_with_full_context_lines() -> None:
    spark = MockSparkLLM([_draft("fire-exit-blocked")])
    zones = {"egress": ZoneSpec(kind="buffer", camera_id="cam1", buffer_of="door", buffer_m=1.5)}
    inventory = [InventoryItem(concept="door", camera_id="cam1", count=1, mean_confidence=0.9)]

    compile_rule(
        "Clear fire exit",
        spark=spark,
        site_name="Warehouse A",
        zones=zones,
        inventory=inventory,
        calibration="cam1-cal",
        plan_id="plan-1",
    )

    user_prompt = _message(spark, 0, 1)
    for expected in (
        "Warehouse A",
        "egress (buffer on cam1)",
        "cam1-cal",
        "door (cam=cam1)",
        "plan-1",
    ):
        assert expected in user_prompt


def test_invalid_model_output_is_repaired_after_one_round() -> None:
    good = _draft("wash-before-line")
    broken = good.model_copy(update={"predicate_root": "p99"})
    spark = MockSparkLLM([broken, good])

    compiled = compile_rule("Wash hands before the line", spark=spark)

    assert compiled == _load_fixture("wash-before-line")
    assert len(spark.compile_calls) == 2
    repair_msg = _message(spark, 1, -1)
    assert "'p99' is referenced but not defined" in repair_msg
    assert "Fix every error" in repair_msg


def test_undeclared_concept_triggers_repair() -> None:
    good = _draft("excavator-swing-radius")
    concepts = {k: v for k, v in good.concepts.items() if k != "person"}
    spark = MockSparkLLM([good.model_copy(update={"concepts": concepts}), good])

    compile_rule("keep two metres from the excavator bucket", spark=spark)

    assert "undeclared concept 'person" in _message(spark, 1, -1)


def test_structured_output_error_triggers_repair() -> None:
    spark = MockSparkLLM([StructuredOutputError("bad json twice"), _draft("person-down")])
    assert compile_rule("alert if someone falls", spark=spark) == _load_fixture("person-down")


def test_repair_failure_raises_rule_compilation_error() -> None:
    broken = _draft("person-down").model_copy(update={"predicate_root": "nope"})
    spark = MockSparkLLM([broken, broken])

    with pytest.raises(RuleCompilationError, match="failed after repair round"):
        compile_rule("alert if someone falls", spark=spark)
    assert len(spark.compile_calls) == 2


def test_transport_errors_are_not_mistaken_for_invalid_output() -> None:
    spark = MockSparkLLM([ModelHTTPError("HTTP 400", status=400, attempts=1)])
    with pytest.raises(ModelHTTPError):
        compile_rule("alert if someone falls", spark=spark)
    assert len(spark.compile_calls) == 1


def test_guardrail_decline_prevents_compilation() -> None:
    spark = MockSparkLLM([])

    with pytest.raises(GuardrailDeclinedError, match="per_person_productivity") as info:
        compile_rule("who is slowest", spark=spark)

    assert info.value.declined.category == GuardrailCategory.PER_PERSON_PRODUCTIVITY
    assert spark.calls == []  # deterministic decline: no model call at all


def test_model_guardrail_decline_prevents_compilation() -> None:
    decline = _ModelClassification(
        verdict="declined", category=GuardrailCategory.IDENTITY, reason="names a person"
    )
    spark = MockSparkLLM([], classification=decline)

    with pytest.raises(GuardrailDeclinedError, match="identity"):
        compile_rule("tell me when Maria arrives", spark=spark)
    assert spark.compile_calls == []


def _node(**fields: Any) -> PredicateNode:
    return PredicateNode.model_validate(fields)


def _with_nodes(root: str, *nodes: PredicateNode) -> RuleDraft:
    return _draft("person-down").model_copy(
        update={"predicate_root": root, "predicate_nodes": list(nodes)}
    )


@pytest.mark.parametrize(
    ("draft", "error"),
    [
        (
            _with_nodes(
                "a",
                _node(id="a", op="not", children=["b"]),
                _node(id="b", op="not", children=["a"]),
            ),
            "cycle",
        ),
        (
            _with_nodes(
                "a",
                _node(id="a", op="pose", object="person", state="down"),
                _node(id="a", op="pose", object="person", state="down"),
            ),
            "used twice",
        ),
        (
            _with_nodes(
                "a",
                _node(id="a", op="pose", object="person", state="down"),
                _node(id="b", op="pose", object="person", state="down"),
            ),
            "not reachable",
        ),
        (
            _with_nodes(
                "a",
                _node(id="a", op="all", children=["b"]),
                _node(id="b", op="pose", object="person", state="down"),
            ),
            "at least two children",
        ),
        (_with_nodes("a", _node(id="a", op="persist", seconds=2.0)), "exactly one child"),
        (_with_nodes("a", _node(id="a", op="sequence", semantics="past")), "trigger"),
        (_with_nodes("a", _node(id="a", op="inside", object="person", zone="dock")), "zone 'dock'"),
    ],
)
def test_draft_to_rule_rejects_malformed_graphs(draft: RuleDraft, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        draft_to_rule(draft)


def test_reference_errors_cover_buffer_zones() -> None:
    rule = _load_fixture("fire-exit-blocked")
    concepts = {k: v for k, v in rule.concepts.items() if k != "door"}
    bad = RuleIR.model_validate({**rule.model_dump(), "concepts": concepts})
    assert any("buffers undeclared concept 'door'" in p for p in reference_errors(bad))


def test_apply_static_hints_tags_fixed_objects_by_whole_word() -> None:
    rule = _load_fixture("wash-before-line")
    concepts = {
        **rule.concepts,
        "sink": ConceptSpec(prompt="hand sink", static=False),
        "heater": ConceptSpec(prompt="outdoor heater"),
        "tracker": ConceptSpec(prompt="trackers"),
        "bin": ConceptSpec(prompt="waste bin"),
    }
    loose = RuleIR.model_validate({**rule.model_dump(), "concepts": concepts})

    tagged = apply_static_hints(loose, static_concepts={"bin"})

    assert tagged.concepts["sink"].static is True
    assert tagged.concepts["bin"].static is True
    assert tagged.concepts["heater"].static is False
    assert tagged.concepts["tracker"].static is False
    assert tagged.concepts["person"].static is False
    assert apply_static_hints(tagged) is tagged


def test_prompt_hygiene_clause_in_compile_system_prompt() -> None:
    assert DATA_NOT_INSTRUCTIONS_CLAUSE in _COMPILE_SYSTEM_PROMPT
