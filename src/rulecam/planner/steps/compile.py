"""Rule compiler step (MP §7.3, V2 §6, task book §4.2, task P2-03).

Turns concerns, inventory items, and zones into typed RuleIR specifications using
Muse Spark structured output, with automated Pydantic validation, static concept tagging,
and a single repair round on schema violations.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence as PySequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from rulecam.contracts.plan import InventoryItem
from rulecam.contracts.protocols import StructuredLLM
from rulecam.contracts.rule_ir import (
    Absence,
    All,
    Any_,
    ConceptSpec,
    Count,
    DistanceM,
    Dwell,
    Inside,
    Moving,
    Not_,
    Overlap,
    Persist,
    PoseState,
    Predicate,
    Rate,
    RuleIR,
    Sequence,
    StateCheck,
    Without,
    ZoneSpec,
)
from rulecam.log import get_logger
from rulecam.planner.errors import GuardrailDeclinedError, RuleCompilationError
from rulecam.planner.guardrails import Declined, check_request
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE, Concern

logger = get_logger(__name__)

DEFAULT_STATIC_CONCEPTS: frozenset[str] = frozenset(
    {"sink", "door", "fire exit door", "racks", "racks/shelving", "shelf", "shelving", "table"}
)

_COMPILE_SYSTEM_PROMPT = (
    "You are Rulecam's Rule IR Compiler.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}\n\n"
    "Your task is to compile a user concern into an inspectable, typed RuleIR specification.\n"
    "The RuleIR format requires:\n"
    "- id: kebab-case identifier (e.g. 'wash-before-line', 'excavator-swing-radius')\n"
    "- version: integer (typically 1)\n"
    "- source: RuleSource(kind, text, lang, plan_id, standard)\n"
    "- concepts: dict[str, ConceptSpec(prompt, static)] mapping short names to prompts. "
    "Static objects (sink, door, racks) have static=True.\n"
    "- zones: dict[str, ZoneSpec(kind, camera_id, polygon_px, buffer_of, buffer_m)]\n"
    "- calibration: camera calibration string if metric distance is used "
    "(e.g. 'ground-plane@cam1')\n"
    "- predicate: typed Predicate with discriminated union on 'op':\n"
    "  * distance_m(a, b, lt, gt)\n"
    "  * inside(object, zone)\n"
    "  * overlap(a, b, min_iou)\n"
    "  * without(subject, item, region)\n"
    "  * moving(object, speed_mps_gt)\n"
    "  * count(object, zone, gt, lt)\n"
    "  * pose(object, state)\n"
    "  * state(object, state)\n"
    "  * all(of: tuple[Predicate, ...])\n"
    "  * any(of: tuple[Predicate, ...])\n"
    "  * not(of: Predicate)\n"
    "  * persist(seconds, of: Predicate)\n"
    "  * dwell(object, zone, total_s_gte, max_gap_s)\n"
    "  * absence(object, zone, seconds)\n"
    "  * rate(of, per_minute_gt)\n"
    "  * sequence(semantics, trigger, require_within_s, requirement)\n"
    "- severity: 'low' | 'medium' | 'high'\n"
    "- actions: tuple of ActionSpec(kind, role, lang, after_s)\n"
    "- verify: VerifySpec(tier1='glimmer', tier2='spark-open-on-disagree'|"
    "'spark-open-on-dispute'|'none', min_confidence=0.7)\n"
    "- accept: list of acceptance criteria names\n"
    "- backtestable: 'yes' | 'partial' | 'no'\n"
    "- schedule: optional operating hours\n"
)


class IntermediatePredicate(BaseModel):
    """Flattened intermediate predicate schema for models that struggle with recursive unions."""

    model_config = ConfigDict(extra="ignore")

    op: str
    a: str | None = None
    b: str | None = None
    lt: float | None = None
    gt: float | None = None
    object: str | None = None
    zone: str | None = None
    min_iou: float = 0.0
    subject: str | None = None
    item: str | None = None
    region: Literal["full", "top_25pct", "hands"] = "full"
    speed_mps_gt: float | None = None
    state: str | None = None
    seconds: float | None = None
    total_s_gte: float | None = None
    max_gap_s: float = 0.0
    per_minute_gt: float | None = None
    semantics: Literal["past", "future"] | None = None
    require_within_s: float | None = None
    trigger: dict[str, Any] | IntermediatePredicate | None = None
    requirement: dict[str, Any] | IntermediatePredicate | None = None
    of: (
        list[dict[str, Any] | IntermediatePredicate] | dict[str, Any] | IntermediatePredicate | None
    ) = None


def _convert_spatial(raw: dict[str, Any]) -> Predicate:
    op = raw.get("op")
    if op == "distance_m":
        return DistanceM(
            a=str(raw.get("a", "")),
            b=str(raw.get("b", "")),
            lt=raw.get("lt"),
            gt=raw.get("gt"),
        )
    if op == "inside":
        return Inside(object=str(raw.get("object", "")), zone=str(raw.get("zone", "")))
    if op == "overlap":
        return Overlap(
            a=str(raw.get("a", "")),
            b=str(raw.get("b", "")),
            min_iou=float(raw.get("min_iou", 0.0)),
        )
    return Without(
        subject=str(raw.get("subject", "")),
        item=str(raw.get("item", "")),
        region=raw.get("region", "full"),
    )


def _convert_state_motion(raw: dict[str, Any]) -> Predicate:
    op = raw.get("op")
    if op == "moving":
        return Moving(
            object=str(raw.get("object", "")),
            speed_mps_gt=float(raw.get("speed_mps_gt", 0.0)),
        )
    if op == "count":
        return Count(
            object=str(raw.get("object", "")),
            zone=raw.get("zone"),
            gt=raw.get("gt"),
            lt=raw.get("lt"),
        )
    if op == "pose":
        return PoseState(
            object=str(raw.get("object", "person")),
            state=raw.get("state", "down"),
        )
    return StateCheck(
        object=str(raw.get("object", "")),
        state=str(raw.get("state", "present")),
    )


def _convert_logical(raw: dict[str, Any]) -> Predicate:
    op = raw.get("op")
    if op in ("all", "any"):
        children_raw = raw.get("of", ())
        if isinstance(children_raw, dict):
            children_raw = [children_raw]
        children = tuple(convert_intermediate_predicate(child) for child in children_raw)
        return All(of=children) if op == "all" else Any_(of=children)
    child_raw = raw.get("of")
    if child_raw is None:
        msg = "Not_ predicate missing 'of'"
        raise ValueError(msg)
    return Not_(of=convert_intermediate_predicate(child_raw))


def _convert_temporal(raw: dict[str, Any]) -> Predicate:
    op = raw.get("op")
    if op == "persist":
        child_raw = raw.get("of")
        if child_raw is None:
            msg = "Persist predicate missing 'of'"
            raise ValueError(msg)
        return Persist(
            seconds=float(raw.get("seconds", 1.0)),
            of=convert_intermediate_predicate(child_raw),
        )
    if op == "dwell":
        return Dwell(
            object=str(raw.get("object", "")),
            zone=str(raw.get("zone", "")),
            total_s_gte=float(raw.get("total_s_gte", 0.0)),
            max_gap_s=float(raw.get("max_gap_s", 0.0)),
        )
    if op == "absence":
        return Absence(
            object=str(raw.get("object", "")),
            zone=str(raw.get("zone", "")),
            seconds=float(raw.get("seconds", 0.0)),
        )
    if op == "rate":
        child_raw = raw.get("of")
        if child_raw is None:
            msg = "Rate predicate missing 'of'"
            raise ValueError(msg)
        return Rate(
            of=convert_intermediate_predicate(child_raw),
            per_minute_gt=float(raw.get("per_minute_gt", 0.0)),
        )
    trigger_raw = raw.get("trigger")
    req_raw = raw.get("requirement")
    if trigger_raw is None or req_raw is None:
        msg = "Sequence predicate missing 'trigger' or 'requirement'"
        raise ValueError(msg)
    return Sequence(
        semantics=raw.get("semantics", "past"),
        trigger=convert_intermediate_predicate(trigger_raw),
        require_within_s=float(raw.get("require_within_s", 60.0)),
        requirement=convert_intermediate_predicate(req_raw),
    )


_CONVERTER_MAP: dict[str, Callable[[dict[str, Any]], Predicate]] = {
    "distance_m": _convert_spatial,
    "inside": _convert_spatial,
    "overlap": _convert_spatial,
    "without": _convert_spatial,
    "moving": _convert_state_motion,
    "count": _convert_state_motion,
    "pose": _convert_state_motion,
    "state": _convert_state_motion,
    "all": _convert_logical,
    "any": _convert_logical,
    "not": _convert_logical,
    "persist": _convert_temporal,
    "dwell": _convert_temporal,
    "absence": _convert_temporal,
    "rate": _convert_temporal,
    "sequence": _convert_temporal,
}


def convert_intermediate_predicate(
    data: IntermediatePredicate | dict[str, Any] | Predicate,
) -> Predicate:
    """Convert an intermediate predicate or dictionary into a concrete typed Predicate."""
    if hasattr(data, "op") and isinstance(
        data,
        (
            DistanceM,
            Inside,
            Overlap,
            Without,
            Moving,
            Count,
            PoseState,
            StateCheck,
            All,
            Any_,
            Not_,
            Persist,
            Dwell,
            Absence,
            Rate,
            Sequence,
        ),
    ):
        return data

    raw: dict[str, Any] = (
        data.model_dump() if isinstance(data, IntermediatePredicate) else dict(data)
    )
    op = str(raw.get("op", ""))
    converter = _CONVERTER_MAP.get(op)
    if converter is not None:
        return converter(raw)

    msg = f"Unknown predicate op: {op}"
    raise ValueError(msg)


def _ensure_rule_ir(obj: Any) -> RuleIR:
    """Validate and convert raw model output or dictionary into a concrete RuleIR."""
    if isinstance(obj, RuleIR):
        return obj

    if isinstance(obj, dict):
        try:
            return RuleIR.model_validate(obj)
        except ValidationError:
            if "predicate" in obj and isinstance(obj["predicate"], (dict, IntermediatePredicate)):
                converted_pred = convert_intermediate_predicate(obj["predicate"])
                obj_copy = dict(obj)
                obj_copy["predicate"] = converted_pred
                return RuleIR.model_validate(obj_copy)
            raise

    return RuleIR.model_validate(obj)


def apply_static_hints(
    rule: RuleIR,
    *,
    inventory: PySequence[InventoryItem] | None = None,
    static_concepts: set[str] | frozenset[str] | None = None,
) -> RuleIR:
    """Tag static concepts (sink, door, racks) based on inventory hints and defaults."""
    known_static: set[str] = set(static_concepts or ()) | set(DEFAULT_STATIC_CONCEPTS)
    if inventory:
        for item in inventory:
            if item.concept in known_static:
                known_static.add(item.concept)

    updated_concepts: dict[str, ConceptSpec] = {}
    changed = False

    for name, spec in rule.concepts.items():
        is_static = (
            spec.static
            or (name in known_static)
            or any(sub in spec.prompt.lower() for sub in ("sink", "door", "rack", "shelf"))
        )
        if is_static != spec.static:
            changed = True
            updated_concepts[name] = ConceptSpec(prompt=spec.prompt, static=is_static)
        else:
            updated_concepts[name] = spec

    if not changed:
        return rule

    return rule.model_copy(update={"concepts": updated_concepts})


def _build_compile_messages(
    *,
    concern_text: str,
    concern_source: str,
    standards: tuple[str, ...],
    plan_id: str | None,
    site_name: str | None,
    calibration: str | None,
    zones: dict[str, ZoneSpec] | None,
    inventory: PySequence[InventoryItem] | None,
) -> list[dict[str, object]]:
    """Format user lines and system prompt for Spark structured compiler call."""
    user_lines = [
        f"Concern text: {concern_text}",
        f"Source reference: {concern_source}",
    ]
    if standards:
        user_lines.append(f"Regulatory standards: {', '.join(standards)}")
    if plan_id:
        user_lines.append(f"Plan ID: {plan_id}")
    if site_name:
        user_lines.append(f"Site: {site_name}")
    if calibration:
        user_lines.append(f"Calibration: {calibration}")
    if zones:
        zone_descriptions = [f"{name} ({spec.kind})" for name, spec in zones.items()]
        user_lines.append(f"Available zones: {', '.join(zone_descriptions)}")
    if inventory:
        inv_descriptions = [f"{item.concept} (cam={item.camera_id})" for item in inventory]
        user_lines.append(f"Surveyed inventory: {', '.join(inv_descriptions)}")

    user_content = "\n".join(user_lines)
    return [
        {"role": "system", "content": _COMPILE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _attempt_compile_with_repair(
    spark: StructuredLLM,
    messages: list[dict[str, object]],
) -> RuleIR:
    """Execute initial structured compilation call with one automated repair round on error."""
    try:
        raw_output = spark.structured(
            model="muse-spark-1.3",
            messages=messages,
            schema=RuleIR,
        )
        return _ensure_rule_ir(raw_output)
    except Exception as first_exc:
        logger.warning("compile_rule_attempt_1_failed_retrying_repair", error=str(first_exc))
        repair_content = (
            f"The previous output failed validation with error:\n{first_exc}\n"
            "Please fix all schema errors and return a valid RuleIR."
        )
        repair_messages: list[dict[str, object]] = [
            *messages,
            {"role": "user", "content": repair_content},
        ]
        try:
            raw_output = spark.structured(
                model="muse-spark-1.3",
                messages=repair_messages,
                schema=RuleIR,
            )
            return _ensure_rule_ir(raw_output)
        except Exception as second_exc:
            logger.exception("compile_rule_repair_round_failed", error=str(second_exc))
            msg = f"Rule compilation failed after repair round: {second_exc}"
            raise RuleCompilationError(msg) from second_exc


def compile_rule(
    concern: Concern | str,
    *,
    spark: StructuredLLM,
    inventory: PySequence[InventoryItem] | None = None,
    zones: dict[str, ZoneSpec] | None = None,
    calibration: str | None = None,
    site_name: str | None = None,
    plan_id: str | None = None,
    static_concepts: set[str] | frozenset[str] | None = None,
    check_guardrails: bool = True,
) -> RuleIR:
    """Compile a concern into a verified RuleIR specification.

    Args:
        concern: The Concern model or natural language rule string.
        spark: StructuredLLM client (Muse Spark 1.3).
        inventory: Detected inventory concepts from survey.
        zones: Declared or suggested zones.
        calibration: Camera calibration name.
        site_name: Optional human-readable site name.
        plan_id: Optional plan identifier for source tracking.
        static_concepts: Optional explicit set of concept names to mark static.
        check_guardrails: Whether to run safety guardrails before compiling.

    Returns:
        A validated, immutable RuleIR object with static tags applied.

    Raises:
        GuardrailDeclinedError: If the request is declined by policy guardrails.
        RuleCompilationError: If compilation fails and cannot be repaired.
    """
    concern_text = concern.text if isinstance(concern, Concern) else concern
    concern_source = concern.source_ref if isinstance(concern, Concern) else "text"
    standards = (
        (concern.standard_ref,) if (isinstance(concern, Concern) and concern.standard_ref) else ()
    )

    if check_guardrails:
        verdict = check_request(concern_text)
        if isinstance(verdict, Declined):
            logger.warning(
                "compile_rule_declined_by_guardrails",
                category=verdict.category,
                reason=verdict.reason,
            )
            msg = f"Request declined under {verdict.category}: {verdict.reason}"
            raise GuardrailDeclinedError(msg)

    messages = _build_compile_messages(
        concern_text=concern_text,
        concern_source=concern_source,
        standards=standards,
        plan_id=plan_id,
        site_name=site_name,
        calibration=calibration,
        zones=zones,
        inventory=inventory,
    )

    rule = _attempt_compile_with_repair(spark, messages)
    return apply_static_hints(rule, inventory=inventory, static_concepts=static_concepts)
