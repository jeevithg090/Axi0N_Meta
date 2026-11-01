"""Canonical rule forms after compile (WS1e, task P2-03).

The compiler wrote the same request, "Keep people at least 2 metres away from a moving
forklift.", in several forms: `all(distance_m(person, forklift) < 2, moving(forklift))`, a 2 m
buffer zone around the forklift with `inside`, the other `all` order, and (live finding WS1d) the
inverted `distance_m(person, forklift, gt=2)`: the compliance condition instead of the violation,
which fires while the person is safely far away (6.4 m in the live trace) and never when close.
The forms behave differently live, and Rule CI proved each against its own semantics.

`canonicalize_rule` rewrites every person-vehicle separation rule into one form:

    [persist(s)] all(distance_m(a=<person>, b=<vehicle>, lt=d), moving(<vehicle>, speed_mps_gt=v))

- **Recognised:** under an optional `persist`, one of `distance_m(lt=d)`, `distance_m(gt=d)`,
  `not(distance_m(...))`, `inside(person, <buffer of vehicle, buffer_m=d>)` or
  `not(inside(...))`, alone or under `all` with at most one `moving(vehicle)`, in any order and
  either argument order, where one concept is person-like and the other vehicle-like
  (`rules.operators`). Anything else is returned unchanged: polygon zones the owner drew stay
  `inside(zone)`.
- **Inverted bounds** (`gt`, `not(lt)`, `not(inside)`) are flipped only when the request reads
  as a separation ("at least", "keep ... away", "no closer than", ...). Otherwise the rule may
  mean something else (a spotter must stay *within* 3 m) and is left alone.
- **d** comes from the request when it names exactly one distance (metres or feet), else from
  the rule. **moving** is kept, or added when the request says the vehicle moves (moving,
  driving, operating, ...); its threshold is `DEFAULT_MOVING_MPS` unless the request names a
  speed.
- **Concepts** are re-keyed by their SAM prompts (`slug(prompt)`), unused concepts and zones are
  dropped, and the subject is the person, so episodes are per person (the engine takes the first
  per-subject child's concept as the subject; `distance_m` first keeps it the person).

The engine applies operator exclusion to this form by default (the driver is not a pedestrian;
`rules.operators`). `canonical_rule_hash` hashes the parts that decide what the rule fires on
(predicate, concepts, zones, calibration), so the same request gives the same hash whatever
form or names the model chose.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final, Literal

from rulecam.contracts.rule_ir import (
    All,
    ConceptSpec,
    DistanceM,
    Inside,
    Moving,
    Not_,
    Persist,
    Predicate,
    RuleIR,
)
from rulecam.log import get_logger
from rulecam.rules.hashing import canonical_json
from rulecam.rules.ir import parse_ref
from rulecam.rules.operators import is_person_like, is_vehicle_like

logger = get_logger(__name__)

DEFAULT_MOVING_MPS: Final = 0.2
FEET_TO_M: Final = 0.3048
SEPARATION_TEXT: Final = re.compile(
    r"(?:\bat\s+least\b|\bminimum\b|\bno\s+(?:closer|nearer)\s+than\b|\bkeep\b.*\b(?:away|clear|"
    r"back|from|distance)\b|\bstay\b.*\b(?:away|clear|back|out)\b|\bclear\s+of\b|\baway\s+from\b"
    r"|>=|≥)"
)
MOTION_TEXT: Final = re.compile(
    r"\b(?:moving|in\s+motion|driving|drives?|driven|travell?ing|operating|reversing|"
    r"backing\s+up|in\s+use)\b"
)
DISTANCE_TEXT: Final = re.compile(r"(\d+(?:\.\d+)?)\s*(metres?|meters?|m|feet|foot|ft)\b(?!\s*/)")
SPEED_TEXT: Final = re.compile(r"\d+(?:\.\d+)?\s*(?:m/s|km/?h|kph|mph)")
_SLUG: Final = re.compile(r"[^a-z0-9]+")

Bound = Literal["near", "far"]


@dataclass(frozen=True)
class _Separation:
    """A recognised separation rule: who keeps away from what, how far, under which bound."""

    person: str
    vehicle: str
    threshold_m: float
    bound: Bound
    moving: Moving | None
    form: str


def _names(rule: RuleIR, concept: str) -> tuple[str, ...]:
    spec = rule.concepts.get(concept)
    return (concept,) if spec is None else (concept, spec.prompt)


def _pair(rule: RuleIR, first: str, second: str) -> tuple[str, str] | None:
    """`(person, vehicle)` if the two references are a person and a vehicle, in either order."""
    (one, one_part), (two, two_part) = parse_ref(first), parse_ref(second)
    if {one_part, two_part} - {None, "foot_point"}:
        return None
    if is_person_like(*_names(rule, one)) and is_vehicle_like(*_names(rule, two)):
        return one, two
    if is_vehicle_like(*_names(rule, one)) and is_person_like(*_names(rule, two)):
        return two, one
    return None


def _flip(bound: Bound) -> Bound:
    return "far" if bound == "near" else "near"


Leaf = tuple[str, str, float, Bound, str]
"""`(person, vehicle, d, bound, form)` of a distance-like leaf."""


def _distance_leaf(rule: RuleIR, leaf: Predicate) -> Leaf | None:
    """Recognise `distance_m`, a buffer `inside`, or the `not` of either."""
    if isinstance(leaf, Not_):
        inner = _distance_leaf(rule, leaf.of)
        return None if inner is None else (*inner[:3], _flip(inner[3]), f"not({inner[4]})")
    if isinstance(leaf, DistanceM):
        return _distance_m_leaf(rule, leaf)
    if isinstance(leaf, Inside):
        return _buffer_leaf(rule, leaf)
    return None


def _distance_m_leaf(rule: RuleIR, leaf: DistanceM) -> Leaf | None:
    pair = _pair(rule, leaf.a, leaf.b)
    if pair is None or (leaf.lt is None) == (leaf.gt is None):
        return None
    if leaf.lt is not None:
        return (*pair, leaf.lt, "near", "distance_m(lt)")
    return (*pair, float(leaf.gt or 0.0), "far", "distance_m(gt)")


def _buffer_leaf(rule: RuleIR, leaf: Inside) -> Leaf | None:
    spec = rule.zones.get(leaf.zone)
    if spec is None or spec.kind != "buffer" or not spec.buffer_of or spec.buffer_m is None:
        return None
    pair = _pair(rule, leaf.object, spec.buffer_of)
    if pair is None or pair[0] != parse_ref(leaf.object)[0]:
        return None
    return (*pair, spec.buffer_m, "near", "inside(buffer)")


def separation_of(rule: RuleIR, node: Predicate) -> _Separation | None:
    """Recognise a person-vehicle separation predicate (under any `persist`, already removed)."""
    kids: tuple[Predicate, ...] = node.of if isinstance(node, All) else (node,)
    movings = [k for k in kids if isinstance(k, Moving)]
    leaves = [k for k in kids if not isinstance(k, Moving)]
    if len(leaves) != 1 or len(movings) > 1:
        return None
    found = _distance_leaf(rule, leaves[0])
    if found is None:
        return None
    person, vehicle, threshold, bound, form = found
    moving = movings[0] if movings else None
    if moving is not None and parse_ref(moving.object)[0] != vehicle:
        return None
    shape = f"all({form},moving)" if moving is not None else form
    return _Separation(person, vehicle, threshold, bound, moving, shape)


def request_distance_m(text: str) -> float | None:
    """The one distance a request names, in metres (None when it names none or several)."""
    found = {
        round(float(value) * (FEET_TO_M if unit.startswith("f") else 1.0), 6)
        for value, unit in DISTANCE_TEXT.findall(text)
    }
    return found.pop() if len(found) == 1 else None


def slug(text: str) -> str:
    """A concept key from a SAM prompt: lower case, words joined by `_`."""
    return _SLUG.sub("_", text.lower()).strip("_") or "object"


def _moving(sep: _Separation, text: str, vehicle_key: str) -> Moving | None:
    """The canonical `moving` conjunct: kept, or added when the request says the vehicle moves."""
    if sep.moving is None and not MOTION_TEXT.search(text):
        return None
    speed = DEFAULT_MOVING_MPS
    if sep.moving is not None and SPEED_TEXT.search(text):
        speed = sep.moving.speed_mps_gt
    return Moving(object=vehicle_key, speed_mps_gt=speed)


def canonicalize_rule(rule: RuleIR, *, request_text: str | None = None) -> RuleIR:
    """Rewrite a person-vehicle separation rule into its canonical form (see the module doc).

    Args:
        rule: A compiled, validated rule.
        request_text: The owner's request; defaults to `rule.source.text`.

    Returns:
        The canonical rule, or `rule` unchanged when it is not a separation rule (or is an
        inverted one whose request does not read as a separation).
    """
    text = (request_text if request_text is not None else rule.source.text or "").lower()
    node: Predicate = rule.predicate
    persist_s: float | None = None
    if isinstance(rule.predicate, Persist):
        node, persist_s = rule.predicate.of, rule.predicate.seconds
    sep = separation_of(rule, node)
    if sep is None or (sep.bound == "far" and not SEPARATION_TEXT.search(text)):
        return rule
    person_key = slug(rule.concepts[sep.person].prompt)
    vehicle_key = slug(rule.concepts[sep.vehicle].prompt)
    if person_key == vehicle_key:
        return rule
    threshold = request_distance_m(text) or sep.threshold_m
    distance = DistanceM(a=person_key, b=vehicle_key, lt=threshold)
    moving = _moving(sep, text, vehicle_key)
    predicate: Predicate = distance if moving is None else All(of=(distance, moving))
    if persist_s is not None:
        predicate = Persist(of=predicate, seconds=persist_s)
    concepts = {
        person_key: _plain(rule.concepts[sep.person]),
        vehicle_key: _plain(rule.concepts[sep.vehicle]),
    }
    canonical = RuleIR.model_validate(
        {
            **rule.model_dump(),
            "predicate": predicate.model_dump(),
            "concepts": {key: spec.model_dump() for key, spec in sorted(concepts.items())},
            "zones": {},
        }
    )
    if canonical != rule:
        logger.info(
            "rule_canonicalized",
            rule_id=rule.id,
            form=sep.form,
            inverted=sep.bound == "far",
            threshold_m=threshold,
        )
    return canonical


def _plain(spec: ConceptSpec) -> ConceptSpec:
    """The concept as the canonical form declares it (a vehicle or a person is never static)."""
    return ConceptSpec(prompt=spec.prompt.strip())


def canonical_rule_hash(rule: RuleIR) -> str:
    """sha256 of what decides the rule's firing: predicate, concepts, zones and calibration.

    Ids, source text, severity, actions and verification are left out, so two compiles of the
    same request that differ only in wording or form hash the same after `canonicalize_rule`.
    """
    data = rule.model_dump(mode="json", include={"predicate", "concepts", "zones", "calibration"})
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
