"""Canonical rule forms after compile (WS1e) and the far-firing reproduction (WS1d). No network.

Live (warehouse clip "forklift driver operating in a warehouse", the only person the driver):
chain traces showed `{'all': True, 'all.0.distance_m': True, 'all.0.distance_m.closest_m':
6.405696, 'all.0.distance_m.separation_m': 6.405696, 'all.0.distance_m.closest_in_s': 0,
'all.0.distance_m.ttc_s': 'none'}` against a 2 m rule. The engine's `distance_m` is true only
when the measured value satisfies its bound, so a true node 6.4 m away means the compiled bound
was `gt=2`: the compliance condition ("at least 2 m") written as the predicate, which the engine
reads as the violation. Co-moving driver: closest_in_s 0 and no collision course.
"""

from __future__ import annotations

from typing import Any, Literal, TypeVar

import pytest
from pydantic import BaseModel

from rulecam.contracts import Track
from rulecam.contracts.rule_ir import All, DistanceM, Moving, Persist, RuleIR
from rulecam.planner.canonical import (
    DEFAULT_MOVING_MPS,
    canonical_rule_hash,
    canonicalize_rule,
    request_distance_m,
    slug,
)
from rulecam.planner.guardrails import _ModelClassification
from rulecam.planner.steps.compile import (
    _COMPILE_SYSTEM_PROMPT,
    COMPILE_EFFORT,
    RuleDraft,
    compile_rule,
    rule_to_draft,
)
from rulecam.rules import evaluate
from tests.rules.helpers import CAL, at, walk_track

M = TypeVar("M", bound=BaseModel)
REQUEST = "Keep people at least 2 metres away from a moving forklift."
CANONICAL = All(
    of=(
        DistanceM(a="person", b="forklift", lt=2.0),
        Moving(object="forklift", speed_mps_gt=DEFAULT_MOVING_MPS),
    )
)
PERSON_FORKLIFT = {"person": {"prompt": "person"}, "forklift": {"prompt": "forklift"}}
BUFFER = {"kind": "buffer", "camera_id": "cam1", "buffer_of": "forklift", "buffer_m": 2.0}


def _rule(
    predicate: dict[str, Any],
    *,
    concepts: dict[str, Any] | None = None,
    zones: dict[str, Any] | None = None,
    text: str = REQUEST,
) -> RuleIR:
    return RuleIR.model_validate(
        {
            "id": "forklift-proximity",
            "source": {"kind": "text", "text": text, "lang": "en"},
            "concepts": concepts or PERSON_FORKLIFT,
            "zones": zones or {},
            "calibration": "ground-plane@cam1",
            "predicate": predicate,
            "severity": "high",
            "actions": [{"kind": "notify", "role": "dock_lead"}, {"kind": "log"}],
        }
    )


def _distance(**bound: float) -> dict[str, Any]:
    return {"op": "distance_m", "a": "person", "b": "forklift", **bound}


def _moving(speed: float = 0.2, concept: str = "forklift") -> dict[str, Any]:
    return {"op": "moving", "object": concept, "speed_mps_gt": speed}


INVERTED = _rule({"op": "all", "of": [_distance(gt=2.0), _moving()]})
FORMS = {
    "distance": _rule({"op": "all", "of": [_distance(lt=2.0), _moving()]}),
    "moving-first": _rule({"op": "all", "of": [_moving(0.3), _distance(lt=2.0)]}),
    "buffer": _rule(
        {"op": "all", "of": [{"op": "inside", "object": "person", "zone": "fz"}, _moving(0.25)]},
        zones={"fz": BUFFER},
    ),
    "inverted-gt": INVERTED,
    "not-gt": _rule({"op": "all", "of": [{"op": "not", "of": _distance(gt=2.0)}, _moving()]}),
    "swapped-args": _rule(
        {
            "op": "all",
            "of": [{"op": "distance_m", "a": "forklift", "b": "person", "lt": 2.0}, _moving()],
        }
    ),
    "foot-points": _rule(
        {
            "op": "all",
            "of": [
                {"op": "distance_m", "a": "person.foot_point", "b": "forklift", "lt": 2.0},
                _moving(0.1),
            ],
        }
    ),
    "other-keys": _rule(
        {
            "op": "all",
            "of": [
                {"op": "distance_m", "a": "people", "b": "fl", "lt": 1.9},
                _moving(0.5, "fl"),
            ],
        },
        concepts={
            "people": {"prompt": "person"},
            "fl": {"prompt": "forklift"},
            "driver": {"prompt": "forklift driver"},
        },
    ),
    "no-moving": _rule(_distance(lt=2.0)),
}


# --- WS1d: the far firing -----------------------------------------------------------------------


def _driver_far_away() -> list[Track]:
    """The live geometry: one person moving with the forklift, 6.4 m from its footprint.

    Both stand still for 1 s, then drive off together, so `moving` turns true with history.
    """
    xs = [1.0] * 10 + [1.0 + 0.1 * i for i in range(30)]
    forklift = walk_track("cam1:forklift:1", [(x, 0.0) for x in xs], concept="forklift")
    person = walk_track("cam1:person:1", [(x, 6.405696) for x in xs])
    return [forklift, person]


def _pedestrian_close() -> list[Track]:
    steps = 40
    forklift = walk_track(
        "cam1:forklift:1", [(1.0 + 0.1 * i, 0.0) for i in range(steps)], concept="forklift"
    )
    return [forklift, walk_track("cam1:person:2", [(3.0, 1.5)] * steps)]


def _run(rule: RuleIR, tracks: list[Track]) -> list[Any]:
    return evaluate(rule, tracks, calibration=CAL, zones={}, t0=at(0), t1=at(4))


def test_the_inverted_bound_reproduces_the_live_far_trace() -> None:
    (event,) = _run(INVERTED, _driver_far_away())
    trace = event.predicate_trace
    assert trace["all"] is True
    assert trace["all.0.distance_m"] is True
    assert trace["all.0.distance_m.separation_m"] == pytest.approx(6.405696)
    assert trace["all.0.distance_m.closest_m"] == pytest.approx(6.405696)
    assert trace["all.0.distance_m.closest_in_s"] == 0
    assert trace["all.0.distance_m.ttc_s"] == "none"


def test_compile_turns_the_inverted_form_into_the_violation_form() -> None:
    rule = compile_rule(REQUEST, spark=_ScriptedSpark([rule_to_draft(INVERTED)]))
    assert rule.predicate == CANONICAL
    assert _run(rule, _driver_far_away()) == []
    assert len(_run(rule, _pedestrian_close())) == 1


def test_a_far_bound_without_separation_wording_is_left_alone() -> None:
    """'A spotter must stay within 3 m of a reversing truck' is a legitimate `gt` rule."""
    spotter = _rule(
        {"op": "distance_m", "a": "truck", "b": "spotter", "gt": 3.0},
        concepts={"truck": {"prompt": "truck"}, "spotter": {"prompt": "worker"}},
        text="A spotter must stay within 3 m of a reversing truck.",
    )
    assert canonicalize_rule(spotter) == spotter


# --- WS1e: one canonical form -------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FORMS))
def test_every_form_of_the_request_becomes_the_canonical_rule(name: str) -> None:
    rule = canonicalize_rule(FORMS[name])
    assert rule.predicate == CANONICAL
    assert set(rule.concepts) == {"person", "forklift"}
    assert rule.zones == {}


def test_all_forms_share_one_canonical_hash() -> None:
    hashes = {canonical_rule_hash(canonicalize_rule(rule)) for rule in FORMS.values()}
    assert len(hashes) == 1
    raw = {canonical_rule_hash(rule) for rule in FORMS.values()}
    assert len(raw) == len(FORMS)


class _ScriptedSpark:
    """`StructuredLLM` fake: allows the guardrail pass, returns queued drafts for compile."""

    def __init__(self, drafts: list[RuleDraft]) -> None:
        self._drafts = list(drafts)
        self.compile_calls: list[dict[str, Any]] = []

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
    ) -> M:
        if schema is _ModelClassification:
            return schema.model_validate({"verdict": "allowed"})
        self.compile_calls.append(
            {"model": model, "messages": messages, "effort": reasoning_effort}
        )
        return schema.model_validate_json(self._drafts.pop(0).model_dump_json())


def test_twenty_compiles_in_rotating_forms_give_one_hash_with_low_effort_and_a_fixed_prefix() -> (
    None
):
    forms = sorted(FORMS)
    drafts = [rule_to_draft(FORMS[forms[i % len(forms)]]) for i in range(20)]
    spark = _ScriptedSpark(drafts)
    hashes = {canonical_rule_hash(compile_rule(REQUEST, spark=spark)) for _ in range(20)}
    assert len(hashes) == 1
    assert len(spark.compile_calls) == 20
    assert {call["effort"] for call in spark.compile_calls} == {COMPILE_EFFORT} == {"low"}
    systems = {call["messages"][0]["content"] for call in spark.compile_calls}
    assert systems == {_COMPILE_SYSTEM_PROMPT}
    users = {call["messages"][1]["content"] for call in spark.compile_calls}
    assert len(users) == 1


def test_the_system_prompt_states_the_violation_semantics_and_the_vehicle_form() -> None:
    assert "describes the VIOLATION" in _COMPILE_SYSTEM_PROMPT
    assert "lt=d), never gt" in _COMPILE_SYSTEM_PROMPT
    assert "moving(object=<vehicle>" in _COMPILE_SYSTEM_PROMPT


# --- details ------------------------------------------------------------------------------------


def test_persist_is_kept_around_the_canonical_form() -> None:
    raw = _rule({"op": "persist", "seconds": 1.0, "of": _distance(lt=2.0)})
    out = canonicalize_rule(raw).predicate
    assert isinstance(out, Persist)
    assert out.of == CANONICAL


def _conjuncts(rule: RuleIR) -> tuple[Any, ...]:
    assert isinstance(rule.predicate, All)
    return rule.predicate.of


def test_the_request_names_the_distance_and_the_speed() -> None:
    feet = _rule(_distance(lt=2.0), text="Keep workers at least 6 feet from a moving forklift")
    assert _conjuncts(canonicalize_rule(feet))[0] == DistanceM(a="person", b="forklift", lt=1.8288)
    fast = _rule(
        {"op": "all", "of": [_distance(lt=2.0), _moving(1.5)]},
        text="Keep people 2 m away from forklifts driving faster than 1.5 m/s",
    )
    assert _conjuncts(canonicalize_rule(fast))[1] == Moving(object="forklift", speed_mps_gt=1.5)
    still = _rule(
        {"op": "distance_m", "a": "person", "b": "truck", "lt": 2.0},
        concepts={"person": {"prompt": "person"}, "truck": {"prompt": "truck"}},
        text="Keep people at least 2 m from a parked truck",
    )
    assert canonicalize_rule(still).predicate == DistanceM(a="person", b="truck", lt=2.0)
    assert request_distance_m("2 m and 3 m") is None
    assert request_distance_m("over 2 m/s") is None


@pytest.mark.parametrize(
    "rule",
    [
        _rule({"op": "all", "of": [_distance(lt=2.0), _moving(concept="person")]}),
        _rule({"op": "any", "of": [_distance(lt=2.0), _moving()]}),
        _rule(_distance(lt=2.0, gt=1.0)),
        _rule(
            {"op": "distance_m", "a": "person.hands", "b": "forklift", "lt": 2.0},
            concepts={**PERSON_FORKLIFT, "hands": {"prompt": "hands"}},
        ),
        _rule(
            {"op": "distance_m", "a": "person", "b": "sink", "lt": 2.0},
            concepts={"person": {"prompt": "person"}, "sink": {"prompt": "sink"}},
        ),
        _rule(
            {"op": "inside", "object": "person", "zone": "dock"},
            zones={
                "dock": {
                    "kind": "polygon",
                    "camera_id": "cam1",
                    "polygon_px": [[0, 0], [1, 0], [1, 1]],
                }
            },
        ),
        _rule(
            {"op": "inside", "object": "forklift", "zone": "pz"},
            zones={"pz": {**BUFFER, "buffer_of": "person"}},
        ),
        _rule(
            _distance(lt=2.0),
            concepts={"person": {"prompt": "forklift"}, "forklift": {"prompt": "forklift"}},
        ),
    ],
    ids=[
        "moving-person",
        "any",
        "two-bounds",
        "hands",
        "not-a-vehicle",
        "drawn-polygon",
        "vehicle-in-person-buffer",
        "same-prompt",
    ],
)
def test_other_rules_are_left_alone(rule: RuleIR) -> None:
    assert canonicalize_rule(rule, request_text=REQUEST) == rule


def test_slug() -> None:
    assert slug("Pallet Jack") == "pallet_jack"
    assert slug("  ") == "object"
