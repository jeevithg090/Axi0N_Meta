"""Rule compiler step (MP §7.3, V2 §6, task book §4.2, task P2-03).

Turns one concern (plus survey inventory and zones) into a typed `RuleIR` through Spark
structured output.

The Rule IR predicate is a recursive discriminated union, and the hosted structured-output API
rejects any recursive (`$ref`-cycle) schema with HTTP 400. So the model is given a flattened
intermediate schema, `RuleDraft`: the RuleIR fields with the predicate replaced by a flat list of
`PredicateNode`s that refer to their children by id. `draft_to_rule` rebuilds the predicate tree
in code (rejecting unknown ids and cycles), validates the result as `RuleIR` with pydantic, and
checks that every concept and zone the predicate names is declared. On any failure one repair
round runs with the errors attached; a second failure raises `RuleCompilationError`.

The guardrails (`planner.guardrails.check_request`, deterministic first, then Spark) run before
anything is compiled. Static concepts (sink, door, racks, shelving) are tagged in code.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from collections.abc import Sequence as Seq
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rulecam.clients.errors import StructuredOutputError
from rulecam.contracts.core import Severity
from rulecam.contracts.plan import InventoryItem
from rulecam.contracts.protocols import StructuredLLM
from rulecam.contracts.rule_ir import (
    ActionSpec,
    All,
    Any_,
    ConceptSpec,
    Not_,
    Persist,
    Predicate,
    Rate,
    RuleIR,
    RuleSource,
    Sequence,
    VerifySpec,
    ZoneSpec,
)
from rulecam.log import get_logger
from rulecam.planner.errors import GuardrailDeclinedError, RuleCompilationError
from rulecam.planner.guardrails import Declined, check_request
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE, Concern

logger = get_logger(__name__)

COMPILE_MODEL: Final = "muse-spark-1.3"
STATIC_WORDS: Final = re.compile(
    r"\b(?:sinks?|doors?|doorways?|racks?|racking|shel(?:f|ves|ving)|walls?|counters?|"
    r"fire\s+exits?|exits?|hand\s*wash\s+stations?|eyewash\s+stations?)\b"
)
_CHILD_LIST_OPS: Final = frozenset({"all", "any"})
_ONE_CHILD_OPS: Final = frozenset({"not", "persist", "rate"})
_OBJECT_FIELDS: Final = ("a", "b", "object", "subject", "item")

PredicateOp = Literal[
    "distance_m",
    "inside",
    "overlap",
    "without",
    "moving",
    "count",
    "pose",
    "state",
    "all",
    "any",
    "not",
    "persist",
    "dwell",
    "absence",
    "rate",
    "sequence",
]


class PredicateNode(BaseModel):
    """One predicate in the flattened, non-recursive form the model fills in.

    Leaf fields are set only for the ops that use them. Composite ops point at other nodes by
    id: `children` for `all`/`any` (two or more) and `not`/`persist`/`rate` (exactly one), and
    `trigger` + `requirement` for `sequence`.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    op: PredicateOp
    a: str | None = None
    b: str | None = None
    object: str | None = None
    subject: str | None = None
    item: str | None = None
    zone: str | None = None
    lt: float | None = None
    gt: float | None = None
    min_iou: float | None = None
    region: Literal["full", "top_25pct", "hands"] | None = None
    speed_mps_gt: float | None = None
    state: str | None = None
    seconds: float | None = None
    total_s_gte: float | None = None
    max_gap_s: float | None = None
    per_minute_gt: float | None = None
    semantics: Literal["past", "future"] | None = None
    require_within_s: float | None = None
    children: list[str] = Field(default_factory=list)
    trigger: str | None = None
    requirement: str | None = None


class RuleDraft(BaseModel):
    """`RuleIR` with the predicate flattened into nodes; the schema the model is given."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int = 1
    source: RuleSource
    concepts: dict[str, ConceptSpec]
    zones: dict[str, ZoneSpec] = Field(default_factory=dict)
    calibration: str | None = None
    predicate_root: str
    predicate_nodes: list[PredicateNode]
    severity: Severity
    actions: list[ActionSpec]
    verify: VerifySpec = VerifySpec()
    accept: list[str] = Field(default_factory=list)
    backtestable: Literal["yes", "partial", "no"] = "yes"
    schedule: str | None = None


_COMPILE_SYSTEM_PROMPT = (
    "You are Rulecam's Rule IR Compiler.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}\n\n"
    "Compile the user's concern into one camera-checkable rule in the RuleDraft schema.\n"
    "- id: kebab-case, e.g. 'wash-before-line'.\n"
    "- concepts: short name -> {prompt, static}; prompt is ONE SAM noun phrase. Fixed objects "
    "(sink, door, racks) are static.\n"
    "- zones: name -> polygon or buffer zone; reuse the zones you are given.\n"
    "- The predicate is a flat list of nodes plus predicate_root, the id of the top node. "
    "Composite nodes refer to other nodes by id: 'all'/'any' list two or more ids in "
    "'children'; 'not', 'persist' and 'rate' list exactly one; 'sequence' sets 'trigger' and "
    "'requirement'. Leaf ops: distance_m(a, b, lt|gt), inside(object, zone), "
    "overlap(a, b, min_iou), without(subject, item, region), moving(object, speed_mps_gt), "
    "count(object, zone?, gt|lt), pose(object, state), state(object, state), "
    "dwell(object, zone, total_s_gte, max_gap_s), absence(object, zone, seconds). "
    "persist needs seconds; rate needs per_minute_gt; sequence needs semantics and "
    "require_within_s.\n"
    "- Every object a predicate names must be a declared concept (or '<concept>.foot_point', "
    "'<concept>.hands', '<concept>.mask'); every zone it names must be declared.\n"
    "- Never write rules that identify people, rank or time individuals, infer health or "
    "emotion, watch private spaces, or use speech."
)


# --- draft <-> RuleIR ---------------------------------------------------------------------------


def _leaf_fields(node: PredicateNode) -> dict[str, object]:
    skip = {"id", "children", "trigger", "requirement"}
    return {k: v for k, v in node.model_dump(exclude_none=True).items() if k not in skip}


def _build(node_id: str, nodes: Mapping[str, PredicateNode], path: tuple[str, ...]) -> object:
    if node_id in path:
        msg = f"predicate node {node_id!r} is part of a cycle: {' -> '.join((*path, node_id))}"
        raise ValueError(msg)
    node = nodes.get(node_id)
    if node is None:
        msg = f"predicate node {node_id!r} is referenced but not defined"
        raise ValueError(msg)
    raw = _leaf_fields(node)
    here = (*path, node_id)
    if node.op in _CHILD_LIST_OPS:
        if len(node.children) < 2:  # noqa: PLR2004 - all/any need two operands
            msg = f"'{node.op}' node {node_id!r} needs at least two children"
            raise ValueError(msg)
        raw["of"] = [_build(c, nodes, here) for c in node.children]
    elif node.op in _ONE_CHILD_OPS:
        if len(node.children) != 1:
            msg = f"'{node.op}' node {node_id!r} needs exactly one child"
            raise ValueError(msg)
        raw["of"] = _build(node.children[0], nodes, here)
    elif node.op == "sequence":
        if node.trigger is None or node.requirement is None:
            msg = f"'sequence' node {node_id!r} needs a trigger and a requirement"
            raise ValueError(msg)
        raw["trigger"] = _build(node.trigger, nodes, here)
        raw["requirement"] = _build(node.requirement, nodes, here)
    return raw


def draft_to_rule(draft: RuleDraft) -> RuleIR:
    """Rebuild the predicate tree from the flat nodes and validate the full `RuleIR`.

    Args:
        draft: The model's flattened rule.

    Returns:
        The validated rule.

    Raises:
        ValueError: If node ids repeat, a reference is dangling, the graph has a cycle, a node
            is not used, or the rule names undeclared concepts or zones.
        ValidationError: If the rebuilt rule does not validate as `RuleIR`.
    """
    nodes: dict[str, PredicateNode] = {}
    for node in draft.predicate_nodes:
        if node.id in nodes:
            msg = f"predicate node id {node.id!r} is used twice"
            raise ValueError(msg)
        nodes[node.id] = node
    tree = _build(draft.predicate_root, nodes, ())
    unused = set(nodes) - set(_node_ids(draft.predicate_root, nodes))
    if unused:
        msg = f"predicate nodes {sorted(unused)} are not reachable from the root"
        raise ValueError(msg)
    fields = draft.model_dump(exclude={"predicate_root", "predicate_nodes"})
    rule = RuleIR.model_validate({**fields, "predicate": tree})
    problems = reference_errors(rule)
    if problems:
        raise ValueError("; ".join(problems))
    return rule


def _node_ids(node_id: str, nodes: Mapping[str, PredicateNode]) -> Iterator[str]:
    yield node_id
    node = nodes[node_id]
    for child in (*node.children, node.trigger, node.requirement):
        if child is not None:
            yield from _node_ids(child, nodes)


def rule_to_draft(rule: RuleIR) -> RuleDraft:
    """Flatten a `RuleIR` into the draft form (the inverse of `draft_to_rule`).

    Args:
        rule: A rule.

    Returns:
        The equivalent draft, with node ids `p0`, `p1`, … in depth-first order.
    """
    nodes: list[PredicateNode] = []

    def visit(pred: Predicate) -> str:
        node_id = f"p{len(nodes)}"
        data = pred.model_dump(exclude={"of", "trigger", "requirement"})
        node = PredicateNode(id=node_id, **data)
        nodes.append(node)
        if isinstance(pred, All | Any_):
            node.children = [visit(child) for child in pred.of]
        elif isinstance(pred, Not_ | Persist | Rate):
            node.children = [visit(pred.of)]
        elif isinstance(pred, Sequence):
            node.trigger = visit(pred.trigger)
            node.requirement = visit(pred.requirement)
        return node_id

    root = visit(rule.predicate)
    fields = rule.model_dump(exclude={"predicate"})
    return RuleDraft.model_validate(
        {**fields, "predicate_root": root, "predicate_nodes": [n.model_dump() for n in nodes]}
    )


def walk_predicates(pred: Predicate) -> Iterator[Predicate]:
    """Yield `pred` and every predicate nested in it, depth first.

    Args:
        pred: A predicate tree.

    Yields:
        Each node of the tree, the root first.
    """
    yield pred
    if isinstance(pred, All | Any_):
        for child in pred.of:
            yield from walk_predicates(child)
    elif isinstance(pred, Not_ | Persist | Rate):
        yield from walk_predicates(pred.of)
    elif isinstance(pred, Sequence):
        yield from walk_predicates(pred.trigger)
        yield from walk_predicates(pred.requirement)


def reference_errors(rule: RuleIR) -> list[str]:
    """List concept and zone names the rule uses but does not declare.

    Object references may carry a part suffix (`person.foot_point`); only the concept name before
    the dot must be declared. Buffer zones must buffer a declared concept.

    Args:
        rule: The rule to check.

    Returns:
        Human-readable problems, empty when every reference resolves.
    """
    problems: list[str] = []
    for pred in walk_predicates(rule.predicate):
        for field in _OBJECT_FIELDS:
            ref = getattr(pred, field, None)
            if isinstance(ref, str) and ref.split(".", 1)[0] not in rule.concepts:
                problems.append(f"{pred.op}.{field} names undeclared concept {ref!r}")
        zone = getattr(pred, "zone", None)
        if isinstance(zone, str) and zone not in rule.zones:
            problems.append(f"{pred.op}.zone names undeclared zone {zone!r}")
    for name, spec in rule.zones.items():
        if spec.kind == "buffer" and spec.buffer_of not in rule.concepts:
            problems.append(f"zone {name!r} buffers undeclared concept {spec.buffer_of!r}")
    return problems


# --- static tagging -----------------------------------------------------------------------------


def apply_static_hints(
    rule: RuleIR, *, static_concepts: set[str] | frozenset[str] | None = None
) -> RuleIR:
    """Mark fixed objects (sink, door, racks, shelving, …) as `static` so SAM segments them once.

    A concept is static if the model said so, if its name or prompt is in `static_concepts`, or
    if its prompt names a fixed object (whole-word match, so "outdoor heater" or "trackers" are
    not static).

    Args:
        rule: The compiled rule.
        static_concepts: Extra concept names or prompts to treat as static.

    Returns:
        The rule, with `static` set where it applies.
    """
    extra = {s.lower() for s in static_concepts or ()}
    concepts = {
        name: spec.model_copy(update={"static": True})
        if not spec.static
        and (
            name.lower() in extra
            or spec.prompt.lower() in extra
            or STATIC_WORDS.search(spec.prompt.lower())
        )
        else spec
        for name, spec in rule.concepts.items()
    }
    if concepts == rule.concepts:
        return rule
    return RuleIR.model_validate({**rule.model_dump(), "concepts": concepts})


# --- compile ------------------------------------------------------------------------------------


def _build_compile_messages(
    *,
    concern_text: str,
    concern_source: str,
    standards: tuple[str, ...],
    plan_id: str | None,
    site_name: str | None,
    calibration: str | None,
    zones: Mapping[str, ZoneSpec] | None,
    inventory: Seq[InventoryItem] | None,
    feedback: Seq[str] = (),
) -> list[dict[str, object]]:
    """Format the system prompt and the user context for the compile call."""
    user_lines = [f"Concern text: {concern_text}", f"Source reference: {concern_source}"]
    if standards:
        user_lines.append(f"Regulatory standards: {', '.join(standards)}")
    if plan_id:
        user_lines.append(f"Plan ID: {plan_id}")
    if site_name:
        user_lines.append(f"Site: {site_name}")
    if calibration:
        user_lines.append(f"Calibration: {calibration}")
    if zones:
        described = ", ".join(
            f"{name} ({spec.kind} on {spec.camera_id})" for name, spec in zones.items()
        )
        user_lines.append(f"Available zones: {described}")
    if inventory:
        seen = ", ".join(f"{item.concept} (cam={item.camera_id})" for item in inventory)
        user_lines.append(f"Surveyed inventory: {seen}")
    if feedback:
        user_lines.append(
            "Your previous rule for this concern failed Rulecam's plan checks. Fix exactly these "
            "problems; keep the rule's severity and actions and never make it less strict:"
        )
        user_lines.extend(f"- {problem}" for problem in feedback)
    return [
        {"role": "system", "content": _COMPILE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def _compile_once(spark: StructuredLLM, messages: list[dict[str, object]]) -> RuleIR:
    draft = spark.structured(model=COMPILE_MODEL, messages=messages, schema=RuleDraft)
    return draft_to_rule(draft)


def _compile_with_repair(spark: StructuredLLM, messages: list[dict[str, object]]) -> RuleIR:
    """Compile once; on an invalid rule, run one repair round with the errors attached."""
    try:
        return _compile_once(spark, messages)
    except (StructuredOutputError, ValidationError, ValueError) as first:
        logger.warning("compile_rule_repair_round", error=str(first))
        repair: list[dict[str, object]] = [
            *messages,
            {
                "role": "user",
                "content": (
                    f"The previous rule was invalid:\n{first}\n"
                    "Fix every error and return the corrected RuleDraft."
                ),
            },
        ]
    try:
        return _compile_once(spark, repair)
    except (StructuredOutputError, ValidationError, ValueError) as second:
        msg = f"Rule compilation failed after repair round: {second}"
        raise RuleCompilationError(msg) from second


def compile_rule(
    concern: Concern | str,
    *,
    spark: StructuredLLM,
    inventory: Seq[InventoryItem] | None = None,
    zones: Mapping[str, ZoneSpec] | None = None,
    calibration: str | None = None,
    site_name: str | None = None,
    plan_id: str | None = None,
    static_concepts: set[str] | frozenset[str] | None = None,
    feedback: Seq[str] = (),
) -> RuleIR:
    """Compile a concern into a validated `RuleIR`, after the guardrails allow it.

    Args:
        concern: The concern, or plain request text.
        spark: Muse Spark 1.3 (`StructuredLLM`); also runs the model guardrail pass.
        inventory: Concepts seen by the survey, given to the model as context.
        zones: Declared zones the rule may use.
        calibration: Camera calibration name, for metric predicates.
        site_name: Optional human-readable site name.
        plan_id: Optional plan identifier for source tracking.
        static_concepts: Extra concept names or prompts to mark static.
        feedback: Problems the plan checks found in the previous rule for this concern (the
            acceptance repair loop, P2-06c); given to the model as data.

    Returns:
        The validated rule, with static concepts tagged.

    Raises:
        GuardrailDeclinedError: If the guardrails decline the request (the decline is attached
            as `declined`).
        RuleCompilationError: If the rule is still invalid after the repair round.
    """
    text = concern.text if isinstance(concern, Concern) else concern
    source = concern.source_ref if isinstance(concern, Concern) else "text"
    standards = (
        (concern.standard_ref,) if isinstance(concern, Concern) and concern.standard_ref else ()
    )

    verdict = check_request(text, spark=spark)
    if isinstance(verdict, Declined):
        logger.warning("compile_rule_declined", category=str(verdict.category))
        raise GuardrailDeclinedError(verdict)

    messages = _build_compile_messages(
        concern_text=text,
        concern_source=source,
        standards=standards,
        plan_id=plan_id,
        site_name=site_name,
        calibration=calibration,
        zones=zones,
        inventory=inventory,
        feedback=feedback,
    )
    rule = _compile_with_repair(spark, messages)
    return apply_static_hints(rule, static_concepts=static_concepts)
