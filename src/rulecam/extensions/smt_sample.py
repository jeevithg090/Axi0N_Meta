"""The symbolic sample a check is proved over, and how a solver model becomes a JSON sample.

A `SymSample` mirrors `extensions.schema.CheckSample` field for field (timestamp, frame_w,
frame_h, objects, zones, lines, px_per_m), with every number a Z3 term. The number of objects is
fixed per sample (`fresh_sample(n, ...)`); the prover enumerates `n = 0 .. max_objects`.

`validity` states what any real sample satisfies: boxes are ordered (`x1 <= x2`, `y1 <= y2`), the
mask's top row lies inside the box, areas are non-negative, scores are in `[0, 1]`, frame sizes
are positive integers, `px_per_m` is positive, a line's two end points differ, and each concept
code names a known concept. Nothing else is assumed, so a proof holds for every such sample.

`shifted` and `moved` build the second sample of a two-sample property (translation, monotone)
from the first one's terms, so the solver searches both at once. `concretize` reads a solver
model back into a plain JSON dict that validates as `CheckSample`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any, Final, Literal

import z3
from pydantic import BaseModel, ConfigDict, Field

from rulecam.extensions.smt_values import SConcept, SDict, SList, SMaybe, SNum, SStr, Value

ObjectField = Literal[
    "x1", "y1", "x2", "y2", "mask_top_y", "mask_area", "ground_x", "ground_y", "score"
]
COUNTEREXAMPLE_TIMESTAMP: Final = "2026-11-03T12:00:00Z"
OTHER_CONCEPT: Final = "other"
MAX_OBJECTS_LIMIT: Final = 4
NICE_FRAME: Final = (1920, 1080)
NICE_MIN_BOX: Final = 20
NICE_MIN_SCORE: Final = 0.5


class MonotoneSpec(BaseModel):
    """A declared monotone direction for one target object.

    Moving the fields one way never turns an alert (value True) into no alert (value False).

    Attributes:
        fields: The object fields that move together (e.g. raising a pallet moves `y1`, `y2` and
            `mask_top_y` up, i.e. they decrease).
        direction: Whether the fields `increase` or `decrease`.
        label: The same change in plain words, for the owner ("raising the pallet").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: tuple[ObjectField, ...] = Field(min_length=1)
    direction: Literal["increase", "decrease"]
    label: str = ""


class ProofSpec(BaseModel):
    """What to prove about one check.

    Attributes:
        target_concept: The concept the check is about, e.g. "pallet".
        other_concepts: Other concepts that may appear in a sample.
        lines: Names of the owner-drawn lines every sample has (e.g. "yellow").
        zones: Names of the zones every sample has, with each zone's vertex count.
        max_objects: The bound K: samples with 0..K objects are covered.
        translation: Whether to prove translation invariance.
        monotone: The declared monotone direction, if any.
        timeout_ms: Solver timeout per query.
        max_paths: Path budget per symbolic execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_concept: str = Field(min_length=1)
    other_concepts: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    zones: Mapping[str, int] = Field(default_factory=dict)
    max_objects: int = Field(default=3, ge=1, le=MAX_OBJECTS_LIMIT)
    translation: bool = True
    monotone: MonotoneSpec | None = None
    timeout_ms: int = Field(default=5000, ge=100, le=60_000)
    max_paths: int = Field(default=2000, ge=10, le=20_000)


Point = tuple[Any, Any]


@dataclass(frozen=True)
class SymObject:
    """One symbolic object: every field is a Z3 term."""

    concept: Any
    box: tuple[Any, Any, Any, Any]
    mask_top: Any
    mask_area: Any
    has_ground: Any
    ground: Point
    has_score: Any
    score: Any


@dataclass(frozen=True)
class SymSample:
    """One symbolic sample of one camera (see `CheckSample`)."""

    objects: tuple[SymObject, ...]
    has_frame_w: Any
    frame_w: Any
    has_frame_h: Any
    frame_h: Any
    has_px_per_m: Any
    px_per_m: Any
    lines: tuple[tuple[str, tuple[Point, Point]], ...]
    zones: tuple[tuple[str, tuple[Point, ...]], ...]
    concepts: tuple[str, ...]


def concept_universe(spec: ProofSpec, literals: Iterable[str]) -> tuple[str, ...]:
    """Every concept name a sample may hold: the spec's, the code's string literals, and one more.

    The extra name (`other`, or a fresh variant) stands for any concept the check never names.

    Args:
        spec: The proof spec.
        literals: String constants found in the check.

    Returns:
        The names, target first, without duplicates.
    """
    names: list[str] = []
    for name in (spec.target_concept, *spec.other_concepts, *sorted(literals)):
        if name and name not in names:
            names.append(name)
    other = OTHER_CONCEPT
    while other in names:
        other = f"{other}_object"
    return (*names, other)


def fresh_sample(n: int, spec: ProofSpec, concepts: tuple[str, ...], prefix: str) -> SymSample:
    """A sample with `n` objects and fresh variables named after `prefix`.

    Args:
        n: The number of objects.
        spec: The proof spec (lines and zones).
        concepts: The concept universe.
        prefix: A prefix keeping these variables apart from others in the same query.

    Returns:
        The symbolic sample.
    """
    objects = tuple(_fresh_object(f"{prefix}o{i}_") for i in range(n))
    lines = tuple(
        (name, (_point(f"{prefix}l_{name}_a"), _point(f"{prefix}l_{name}_b")))
        for name in spec.lines
    )
    zones = tuple(
        (name, tuple(_point(f"{prefix}z_{name}_{k}") for k in range(count)))
        for name, count in spec.zones.items()
    )
    return SymSample(
        objects=objects,
        has_frame_w=z3.Bool(f"{prefix}has_frame_w"),
        frame_w=z3.Real(f"{prefix}frame_w"),
        has_frame_h=z3.Bool(f"{prefix}has_frame_h"),
        frame_h=z3.Real(f"{prefix}frame_h"),
        has_px_per_m=z3.Bool(f"{prefix}has_px_per_m"),
        px_per_m=z3.Real(f"{prefix}px_per_m"),
        lines=lines,
        zones=zones,
        concepts=concepts,
    )


def _point(name: str) -> Point:
    return (z3.Real(f"{name}_x"), z3.Real(f"{name}_y"))


def _fresh_object(prefix: str) -> SymObject:
    return SymObject(
        concept=z3.Int(f"{prefix}concept"),
        box=(
            z3.Real(f"{prefix}x1"),
            z3.Real(f"{prefix}y1"),
            z3.Real(f"{prefix}x2"),
            z3.Real(f"{prefix}y2"),
        ),
        mask_top=z3.Real(f"{prefix}mask_top"),
        mask_area=z3.Real(f"{prefix}mask_area"),
        has_ground=z3.Bool(f"{prefix}has_ground"),
        ground=(z3.Real(f"{prefix}gx"), z3.Real(f"{prefix}gy")),
        has_score=z3.Bool(f"{prefix}has_score"),
        score=z3.Real(f"{prefix}score"),
    )


def validity(sample: SymSample) -> list[Any]:
    """The constraints every real sample satisfies (see the module docstring).

    Args:
        sample: The symbolic sample.

    Returns:
        Z3 Bool terms.
    """
    facts: list[Any] = []
    for obj in sample.objects:
        facts.extend(_object_validity(obj, len(sample.concepts)))
    for has, size in ((sample.has_frame_w, sample.frame_w), (sample.has_frame_h, sample.frame_h)):
        facts.append(z3.Implies(has, z3.And(size > 0, z3.IsInt(size))))
    facts.append(z3.Implies(sample.has_px_per_m, sample.px_per_m > 0))
    for _, (a, b) in sample.lines:
        facts.append(z3.Or(a[0] != b[0], a[1] != b[1]))
    return facts


def _object_validity(obj: SymObject, concepts: int) -> list[Any]:
    x1, y1, x2, y2 = obj.box
    return [
        obj.concept >= 0,
        obj.concept < concepts,
        x1 <= x2,
        y1 <= y2,
        y1 <= obj.mask_top,
        obj.mask_top <= y2,
        obj.mask_area >= 0,
        z3.Implies(obj.has_score, z3.And(obj.score >= 0, obj.score <= 1)),
    ]


def nice(sample: SymSample) -> list[Any]:
    """Extra constraints that make a counterexample look like a real camera sample.

    Everything inside a 1920 x 1080 frame, boxes at least 20 px a side with the mask's top on
    the box's top and the box's area (as the API builds them today), scores of 0.5 or more.
    Used only to choose which counterexample to show, never as an assumption of a proof.

    Args:
        sample: The symbolic sample.

    Returns:
        Z3 Bool terms.
    """
    facts: list[Any] = []
    for obj in sample.objects:
        x1, y1, x2, y2 = obj.box
        facts.extend([*_inside(x1, y1), *_inside(x2, y2)])
        facts.extend([x2 - x1 >= NICE_MIN_BOX, y2 - y1 >= NICE_MIN_BOX, obj.mask_top == y1])
        facts.append(obj.mask_area == (x2 - x1) * (y2 - y1))
        facts.append(z3.Implies(obj.has_score, obj.score >= NICE_MIN_SCORE))
    for _, points in (*sample.lines, *sample.zones):
        for point in points:
            facts.extend(_inside(*point))
    return facts


def _inside(x: Any, y: Any) -> list[Any]:
    return [x >= 0, x <= NICE_FRAME[0], y >= 0, y <= NICE_FRAME[1]]


def no_target(sample: SymSample) -> Any:
    """No object of the sample has the target concept (code 0).

    Args:
        sample: The symbolic sample.

    Returns:
        A Z3 Bool term.
    """
    return z3.And(*[obj.concept != 0 for obj in sample.objects], True)


def shifted(sample: SymSample, dx: Any, dy: Any) -> SymSample:
    """The same scene moved by `(dx, dy)` image pixels (boxes, masks, lines and zones).

    Ground points, frame sizes and `px_per_m` stay: they do not move with the image.

    Args:
        sample: The original sample.
        dx: Horizontal shift (a Z3 term).
        dy: Vertical shift (a Z3 term).

    Returns:
        The shifted sample.
    """
    objects = tuple(
        replace(
            obj,
            box=(obj.box[0] + dx, obj.box[1] + dy, obj.box[2] + dx, obj.box[3] + dy),
            mask_top=obj.mask_top + dy,
        )
        for obj in sample.objects
    )
    lines = tuple(
        (name, ((a[0] + dx, a[1] + dy), (b[0] + dx, b[1] + dy))) for name, (a, b) in sample.lines
    )
    zones = tuple(
        (name, tuple((p[0] + dx, p[1] + dy) for p in points)) for name, points in sample.zones
    )
    return replace(sample, objects=objects, lines=lines, zones=zones)


def moved(sample: SymSample, index: int, spec: MonotoneSpec, delta: Any) -> SymSample:
    """The same scene with object `index`'s declared fields moved by `delta` (> 0).

    Args:
        sample: The original sample.
        index: Which object moves.
        spec: The monotone spec (fields and direction).
        delta: The positive amount (a Z3 term).

    Returns:
        The changed sample.
    """
    step = delta if spec.direction == "increase" else -delta
    obj = sample.objects[index]
    values = _field_terms(obj)
    for name in spec.fields:
        values[name] = values[name] + step
    changed = SymObject(
        concept=obj.concept,
        box=(values["x1"], values["y1"], values["x2"], values["y2"]),
        mask_top=values["mask_top_y"],
        mask_area=values["mask_area"],
        has_ground=obj.has_ground,
        ground=(values["ground_x"], values["ground_y"]),
        has_score=obj.has_score,
        score=values["score"],
    )
    objects = tuple(changed if i == index else o for i, o in enumerate(sample.objects))
    return replace(sample, objects=objects)


def _field_terms(obj: SymObject) -> dict[str, Any]:
    return {
        "x1": obj.box[0],
        "y1": obj.box[1],
        "x2": obj.box[2],
        "y2": obj.box[3],
        "mask_top_y": obj.mask_top,
        "mask_area": obj.mask_area,
        "ground_x": obj.ground[0],
        "ground_y": obj.ground[1],
        "score": obj.score,
    }


def to_value(sample: SymSample) -> SDict:
    """The sample as the symbolic dict a check receives (every key present, like `as_json`).

    Args:
        sample: The symbolic sample.

    Returns:
        The symbolic dict.
    """
    return SDict(
        (
            ("timestamp", SStr(None)),
            ("frame_w", SMaybe(z3.Not(sample.has_frame_w), SNum(sample.frame_w, "int"))),
            ("frame_h", SMaybe(z3.Not(sample.has_frame_h), SNum(sample.frame_h, "int"))),
            ("objects", SList(tuple(_object_value(o, sample.concepts) for o in sample.objects))),
            ("zones", SDict(tuple((n, _polygon(points)) for n, points in sample.zones))),
            ("lines", SDict(tuple((n, _polygon(points)) for n, points in sample.lines))),
            ("px_per_m", SMaybe(z3.Not(sample.has_px_per_m), SNum(sample.px_per_m))),
        )
    )


def _polygon(points: Iterable[Point]) -> SList:
    return SList(tuple(SList((SNum(p[0]), SNum(p[1]))) for p in points))


def _object_value(obj: SymObject, concepts: tuple[str, ...]) -> Value:
    box = SList(tuple(SNum(term) for term in obj.box))
    ground = SMaybe(z3.Not(obj.has_ground), SList((SNum(obj.ground[0]), SNum(obj.ground[1]))))
    return SDict(
        (
            ("concept", SConcept(obj.concept, concepts)),
            ("box_xyxy_px", box),
            ("mask_top_y_px", SNum(obj.mask_top)),
            ("mask_area_px", SNum(obj.mask_area)),
            ("ground_xy_m", ground),
            ("score", SMaybe(z3.Not(obj.has_score), SNum(obj.score))),
        )
    )


def concretize(model: Any, sample: SymSample) -> dict[str, object]:
    """Read a solver model back into a plain JSON sample (valid as `CheckSample`).

    Args:
        model: A Z3 model of a query over `sample`.
        sample: The symbolic sample.

    Returns:
        The sample dict, with every key present.
    """

    def num(term: Any) -> float:
        return to_float(model.eval(term, model_completion=True))

    def flag(term: Any) -> bool:
        return bool(z3.is_true(model.eval(term, model_completion=True)))

    def point(p: Point) -> list[float]:
        return [num(p[0]), num(p[1])]

    def obj_json(obj: SymObject) -> dict[str, object]:
        code = int(model.eval(obj.concept, model_completion=True).as_long())
        return {
            "concept": sample.concepts[code],
            "box_xyxy_px": [num(term) for term in obj.box],
            "mask_top_y_px": num(obj.mask_top),
            "mask_area_px": num(obj.mask_area),
            "ground_xy_m": point(obj.ground) if flag(obj.has_ground) else None,
            "score": num(obj.score) if flag(obj.has_score) else None,
        }

    def size(has: Any, term: Any) -> int | None:
        return round(num(term)) if flag(has) else None

    return {
        "timestamp": COUNTEREXAMPLE_TIMESTAMP,
        "frame_w": size(sample.has_frame_w, sample.frame_w),
        "frame_h": size(sample.has_frame_h, sample.frame_h),
        "objects": [obj_json(obj) for obj in sample.objects],
        "zones": {name: [point(p) for p in pts] for name, pts in sample.zones},
        "lines": {name: [point(p) for p in pts] for name, pts in sample.lines},
        "px_per_m": num(sample.px_per_m) if flag(sample.has_px_per_m) else None,
    }


def to_float(value: Any) -> float:
    """A Z3 numeral (integer, rational or algebraic) as the nearest Python float.

    Args:
        value: An evaluated Z3 arithmetic term.

    Returns:
        The float.
    """
    if z3.is_int_value(value):
        return float(value.as_long())
    if z3.is_rational_value(value):
        return float(Fraction(value.numerator_as_long(), value.denominator_as_long()))
    if z3.is_algebraic_value(value):
        return to_float(value.approx(20))
    return float(Fraction(str(z3.simplify(value))))
